"""Entry point for bitacora.exe, the windowed program (built with PyInstaller --windowed)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from bitacora.window import open_window

    sys.exit(open_window(sys.argv[1:]))
