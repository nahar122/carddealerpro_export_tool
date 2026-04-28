"""Drag-and-drop GUI for transform_cards.

Drop a card-export CSV onto the window (or click Browse...) and the app
will run the rule-2 / rule-3 transformations and prompt you for a save
location for the transformed CSV.

Build for distribution:
    Windows:  build.bat   ->  dist\\CardCSVTransformer.exe
    macOS:    ./build.sh  ->  dist/CardCSVTransformer.app
    Linux:    ./build.sh  ->  dist/CardCSVTransformer

The resulting binary is self-contained — no Python install needed on
the recipient's machine. PyInstaller cannot cross-compile, so the build
must run on the same OS as the target.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

# Use a font family that exists on the current OS. Segoe UI is Windows-only;
# on macOS it falls back to a generic font that looks out of place.
if sys.platform == "darwin":
    UI_FONT = "Helvetica Neue"
elif sys.platform.startswith("linux"):
    UI_FONT = "DejaVu Sans"
else:
    UI_FONT = "Segoe UI"

from transform_cards import VOCAB_PATH, load_parallel_vocab, transform_csv

# tkinterdnd2 provides native drag-and-drop. Fall back gracefully if it's
# not installed so the Browse... button still works.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    TkinterDnD = None  # type: ignore[assignment]
    DND_FILES = None  # type: ignore[assignment]
    HAS_DND = False


def _parse_dnd_paths(data: str) -> list[str]:
    """Parse the path string emitted by tkinterdnd2 on a drop event.

    Single paths arrive as plain strings; paths with spaces are wrapped in
    `{...}`; multi-file drops are space-separated.
    """
    matches = re.findall(r"\{([^}]+)\}|(\S+)", data)
    return [m[0] or m[1] for m in matches if (m[0] or m[1])]


class App:
    DROP_BG = "#eef3f8"
    DROP_HOVER_BG = "#dce6f3"

    def __init__(self) -> None:
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.title("Card CSV Transformer")
        self.root.geometry("560x380")
        self.root.minsize(480, 320)

        # Header
        header = tk.Frame(self.root)
        header.pack(fill="x", padx=20, pady=(18, 0))
        tk.Label(header, text="Card CSV Transformer",
                 font=(UI_FONT, 16, "bold")).pack(anchor="w")
        tk.Label(header, text=("Transforms a card-export CSV into the "
                               "5-column listing format."),
                 fg="#555").pack(anchor="w", pady=(2, 0))

        # Drop zone
        self.drop_frame = tk.Frame(self.root, bg=self.DROP_BG,
                                   relief="ridge", bd=2)
        self.drop_frame.pack(padx=20, pady=15, fill="both", expand=True)

        msg = ("Drop a CSV file here"
               if HAS_DND
               else "Click Browse… to pick a CSV file")
        self.drop_label = tk.Label(self.drop_frame, text=msg, bg=self.DROP_BG,
                                   font=(UI_FONT, 14), fg="#333")
        self.drop_label.pack(expand=True)
        self.drop_hint = tk.Label(self.drop_frame,
                                  text="(or click Browse… below)",
                                  bg=self.DROP_BG, fg="#888",
                                  font=(UI_FONT, 9))
        self.drop_hint.pack(pady=(0, 18))

        if HAS_DND:
            for w in (self.drop_frame, self.drop_label, self.drop_hint):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self.on_drop)
                w.dnd_bind("<<DragEnter>>", self.on_drag_enter)
                w.dnd_bind("<<DragLeave>>", self.on_drag_leave)

        # Buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=(0, 10))
        tk.Button(btn_frame, text="Browse…", command=self.on_browse,
                  width=14).pack()

        # Status bar
        self.status = tk.Label(self.root, text="Ready", anchor="w",
                               bd=1, relief="sunken", padx=8)
        self.status.pack(fill="x", side="bottom")

    # ---------- Event handlers ----------

    def on_drag_enter(self, _event: object) -> None:
        for w in (self.drop_frame, self.drop_label, self.drop_hint):
            w.configure(bg=self.DROP_HOVER_BG)

    def on_drag_leave(self, _event: object) -> None:
        for w in (self.drop_frame, self.drop_label, self.drop_hint):
            w.configure(bg=self.DROP_BG)

    def on_drop(self, event) -> None:
        self.on_drag_leave(event)
        paths = _parse_dnd_paths(event.data)
        if not paths:
            messagebox.showerror("Error", "Could not parse the dropped file.")
            return
        if len(paths) > 1:
            messagebox.showinfo(
                "Note",
                f"Multiple files dropped — only the first will be processed:\n\n{paths[0]}"
            )
        self.process_file(paths[0])

    def on_browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a card-export CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.process_file(path)

    # ---------- Core flow ----------

    def process_file(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            messagebox.showerror("Error", f"File not found:\n{path}")
            return
        if path.suffix.lower() != ".csv":
            messagebox.showerror(
                "Error",
                f"Expected a .csv file but got: {path.suffix or '<no extension>'}"
            )
            return

        suggested_name = f"{path.stem}_transformed.csv"
        save_path = filedialog.asksaveasfilename(
            initialfile=suggested_name,
            initialdir=str(path.parent),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Save transformed CSV as",
        )
        if not save_path:
            self.set_status("Cancelled.")
            return

        try:
            self.set_status(f"Processing {path.name}…")
            self.root.update_idletasks()

            tokens, qualifiers = load_parallel_vocab(VOCAB_PATH)
            n = transform_csv(path, Path(save_path), tokens, qualifiers,
                              debug=False)

            self.set_status(f"Wrote {n} rows.")
            messagebox.showinfo(
                "Done",
                f"Transformed {n} rows.\n\nSaved to:\n{save_path}",
            )
        except Exception as exc:  # noqa: BLE001  -- surface every failure
            self.set_status("Error.")
            messagebox.showerror(
                "Transformation failed",
                f"{type(exc).__name__}: {exc}\n\n"
                f"Details:\n{traceback.format_exc()}",
            )

    def set_status(self, text: str) -> None:
        self.status.config(text=text)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if not VOCAB_PATH.exists():
        messagebox.showerror(
            "Missing vocab file",
            f"Could not find parallel_vocab.txt at:\n{VOCAB_PATH}\n\n"
            "If you're running the .exe, this is a build issue — please "
            "let the developer know."
        )
        return 1
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
