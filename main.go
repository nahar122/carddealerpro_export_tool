// Card CSV Transformer — local web UI.
//
// Launches an embedded web server on a random localhost port, opens the
// user's default browser to the upload page, accepts a CSV upload, and
// streams back the transformed CSV as a download. Single static binary;
// the vocab file and HTML template are baked in via //go:embed.
package main

import (
	"bytes"
	"context"
	"embed"
	"fmt"
	"html/template"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"cardcsv/transform"
)

//go:embed parallel_vocab.txt
var embeddedVocab []byte

//go:embed web/index.html
var webFS embed.FS

const maxUploadSize = 25 << 20 // 25 MB

type server struct {
	tokens     map[string]struct{}
	qualifiers map[string]struct{}
	tmpl       *template.Template
	httpServer *http.Server
	shutdownCh chan struct{}
}

type pageData struct {
	Error string
}

func (s *server) renderIndex(w http.ResponseWriter, errMsg string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.tmpl.Execute(w, pageData{Error: errMsg}); err != nil {
		log.Printf("template execute: %v", err)
	}
}

func (s *server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	s.renderIndex(w, "")
}

func (s *server) handleTransform(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		s.renderIndex(w, fmt.Sprintf("Upload too large or malformed: %v", err))
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		s.renderIndex(w, "Please choose a CSV file.")
		return
	}
	defer file.Close()

	var buf bytes.Buffer
	rowCount, err := transform.TransformCSV(file, &buf, s.tokens, s.qualifiers)
	if err != nil {
		s.renderIndex(w, fmt.Sprintf("Could not transform CSV: %v", err))
		return
	}

	outName := outputFilename(header.Filename)
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, outName))
	w.Header().Set("X-Row-Count", fmt.Sprintf("%d", rowCount))
	w.Header().Set("Content-Length", fmt.Sprintf("%d", buf.Len()))
	if _, err := w.Write(buf.Bytes()); err != nil {
		log.Printf("write response: %v", err)
	}
	log.Printf("transformed %s -> %s (%d rows)", header.Filename, outName, rowCount)
}

func (s *server) handleShutdown(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprint(w, `<!DOCTYPE html><html><body style="font-family:sans-serif;text-align:center;margin-top:4rem;color:#555;">
<h2>Card CSV Transformer stopped.</h2>
<p>You can close this tab.</p>
</body></html>`)
	go func() {
		time.Sleep(200 * time.Millisecond)
		close(s.shutdownCh)
	}()
}

// outputFilename derives "<stem>_transformed.csv" from an uploaded
// filename. Strips any path components defensively.
func outputFilename(uploaded string) string {
	base := filepath.Base(uploaded)
	if base == "." || base == "/" || base == "" {
		base = "input.csv"
	}
	ext := filepath.Ext(base)
	stem := strings.TrimSuffix(base, ext)
	if stem == "" {
		stem = "input"
	}
	return stem + "_transformed.csv"
}

// openBrowser launches the OS's default browser at url. Best-effort:
// if the call fails, the URL is still printed to stdout for the user.
func openBrowser(url string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", url)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	default: // linux, freebsd, etc.
		cmd = exec.Command("xdg-open", url)
	}
	return cmd.Start()
}

func main() {
	tokens, qualifiers := transform.LoadVocab(bytes.NewReader(embeddedVocab))
	tmpl, err := template.ParseFS(webFS, "web/index.html")
	if err != nil {
		log.Fatalf("parse template: %v", err)
	}

	s := &server{
		tokens:     tokens,
		qualifiers: qualifiers,
		tmpl:       tmpl,
		shutdownCh: make(chan struct{}),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/transform", s.handleTransform)
	mux.HandleFunc("/shutdown", s.handleShutdown)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	addr := listener.Addr().(*net.TCPAddr)
	url := fmt.Sprintf("http://127.0.0.1:%d/", addr.Port)

	s.httpServer = &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	fmt.Fprintf(os.Stdout, "Card CSV Transformer running at %s\n", url)
	fmt.Fprintln(os.Stdout, "(close the browser tab or click Quit when finished)")

	go func() {
		if err := s.httpServer.Serve(listener); err != nil && err != http.ErrServerClosed {
			log.Printf("server: %v", err)
		}
	}()

	if err := openBrowser(url); err != nil {
		log.Printf("could not open browser automatically: %v", err)
		fmt.Fprintf(os.Stdout, "Please open %s manually.\n", url)
	}

	<-s.shutdownCh
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = s.httpServer.Shutdown(ctx)
	fmt.Fprintln(os.Stdout, "Goodbye.")
}
