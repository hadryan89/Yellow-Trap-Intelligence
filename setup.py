"""
Instalacao opcional como pacote:

    pip install -e .

Instalar em modo editavel resolve o sys.path automaticamente (os scripts
deixam de precisar do ajuste manual) e cria os comandos de console.
"""

from pathlib import Path

from setuptools import find_packages, setup

RAIZ = Path(__file__).resolve().parent
LEIAME = (RAIZ / "README.md").read_text(encoding="utf-8") if (RAIZ / "README.md").exists() else ""
REQUISITOS = [
    linha.strip()
    for linha in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
    if linha.strip() and not linha.startswith("#")
]

setup(
    name="yellowtrap-pipeline",
    version="2.0.0",
    description="Pipeline de processamento de imagens de armadilhas YellowTrap "
                "(nomeacao + recorte dos quadrantes)",
    long_description=LEIAME,
    long_description_content_type="text/markdown",
    author="Setor de Inovacao - Grupo Progresso",
    packages=find_packages(include=["src", "src.*", "config", "config.*"]),
    python_requires=">=3.11",
    install_requires=REQUISITOS,
    entry_points={
        "console_scripts": [
            "yellowtrap-pipeline=scripts.run_pipeline:main",
            "yellowtrap-recorte=scripts.run_apenas_recorte:main",
            "yellowtrap-watcher=scripts.watcher:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3.11",
        "Operating System :: Microsoft :: Windows",
        "Topic :: Scientific/Engineering :: Image Processing",
    ],
)
