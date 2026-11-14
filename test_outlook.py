"""Comando antiguo. Usa las cuentas y credenciales del panel."""
import sys
from lector.lector import main

if __name__ == "__main__":
    sys.argv[1:] = ["probar", *sys.argv[1:]]
    main()
