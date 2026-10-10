import multiprocessing

from bitacora.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
