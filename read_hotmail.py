"""Comando antiguo. Listar una cuenta configurada: python read_hotmail.py CUENTA."""
import sys
from correo_reenvio import main

if __name__ == "__main__":
    sys.argv[1:] = ["listar", *sys.argv[1:]]
    main()
