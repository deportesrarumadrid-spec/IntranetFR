import sys, os

# Añadimos la carpeta actual al camino de Python
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application