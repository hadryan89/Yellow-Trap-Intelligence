"""
Configuracao comum dos testes.

- Garante a raiz do projeto no sys.path (funciona mesmo sem pip install -e .).
- Gera as fixtures sinteticas na primeira execucao.
- Isola as pastas de dados em tmp_path, para que nenhum teste escreva em
  data/ ou logs/ do projeto real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402
from tests.fixtures import gerar_fixtures  # noqa: E402

PASTA_FIXTURES = RAIZ / "tests" / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _fixtures_geradas():
    """Cria as imagens sinteticas se ainda nao existirem."""
    gerar_fixtures.gerar_todas(PASTA_FIXTURES)
    return PASTA_FIXTURES


@pytest.fixture
def pasta_fixtures() -> Path:
    return PASTA_FIXTURES


@pytest.fixture
def foto_valida() -> Path:
    """Foto sintetica com a grade detectavel, fundo AMARELO."""
    return PASTA_FIXTURES / "foto_grade_valida.png"


@pytest.fixture
def foto_azul() -> Path:
    """Mesma geometria da foto_valida, com fundo de armadilha AZUL."""
    return PASTA_FIXTURES / "foto_grade_azul.png"


@pytest.fixture
def foto_inclinada() -> Path:
    """Grade girada: exercita a correcao de inclinacao."""
    return PASTA_FIXTURES / "foto_grade_inclinada.png"


@pytest.fixture
def foto_sem_grade() -> Path:
    """Foto sintetica sem linhas detectaveis."""
    return PASTA_FIXTURES / "foto_sem_grade.png"


@pytest.fixture
def pastas_isoladas(tmp_path, monkeypatch):
    """
    Redireciona TODAS as pastas de dados para tmp_path.

    Qualquer teste que grave arquivos deve pedir esta fixture.
    """
    mapa = {
        "PASTA_DADOS": tmp_path,
        "PASTA_ENTRADA": tmp_path / "01_entrada_bruta",
        "PASTA_RENOMEADAS": tmp_path / "02_renomeadas",
        "PASTA_RECORTADAS": tmp_path / "03_recortadas",
        "PASTA_RELATORIOS": tmp_path / "_relatorios",
        "PASTA_FALHAS": tmp_path / "_falhas",
        "PASTA_ZIPS": tmp_path / "_zips",
        "PASTA_LOGS": tmp_path / "logs",
    }
    for nome, caminho in mapa.items():
        caminho.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, nome, caminho)
    monkeypatch.setattr(settings, "PASTAS_OBRIGATORIAS", list(mapa.values())[1:])
    monkeypatch.setattr(settings, "ARQUIVO_LOG", mapa["PASTA_LOGS"] / "pipeline.log")
    return mapa


def criar_quadrante(cor_bgr, largura: int = 20, altura: int = 12) -> np.ndarray:
    """Cria um quadrante solido de cor conhecida (helper dos testes)."""
    return np.full((altura, largura, 3), cor_bgr, dtype=np.uint8)
