"""
Testes do protocolo 3 (stitching + exportacao).

O ponto critico e o LAYOUT HORIZONTAL: letras (a-d) sao as 4 LINHAS e
numeros (1-10) sao as 10 COLUNAS. Trocar isso monta a placa transposta e
inviabiliza a leitura das pragas por quadrante.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from config import settings
from src.exportacao import exportar_multiplas_resolucoes
from src.stitching import (
    carregar_quadrantes,
    celulas_faltantes,
    executar_stitching,
    montar_placa,
    normalizar_tamanhos,
)
from tests.conftest import cor_da_celula, criar_quadrante

ALTURA_CELULA, LARGURA_CELULA = 12, 20


def _cor_unica(letra: str, numero: int) -> tuple[int, int, int]:
    """Cor deterministica e unica por celula, para rastrear a posicao."""
    indice_letra = settings.LETRAS_COLUNAS.index(letra)
    return (10 + indice_letra * 40, 10 + numero * 20, 200 - indice_letra * 30)


def _grid_completo() -> dict:
    return {
        (letra, numero): criar_quadrante(_cor_unica(letra, numero),
                                         LARGURA_CELULA, ALTURA_CELULA)
        for letra in settings.LETRAS_COLUNAS
        for numero in settings.NUMEROS_LINHAS
    }


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_placa_tem_quatro_linhas_por_dez_colunas():
    placa = montar_placa(_grid_completo(),
                         ordem_letras=settings.LETRAS_COLUNAS,
                         ordem_numeros=settings.NUMEROS_LINHAS)
    assert placa.shape[0] == 4 * ALTURA_CELULA   # 4 faixas empilhadas
    assert placa.shape[1] == 10 * LARGURA_CELULA  # 10 celulas lado a lado
    assert placa.shape[1] > placa.shape[0], "a placa e mais larga que alta"


def test_cada_celula_esta_na_posicao_certa():
    """a1 no canto superior esquerdo, d10 no canto inferior direito."""
    placa = montar_placa(_grid_completo(),
                         ordem_letras=settings.LETRAS_COLUNAS,
                         ordem_numeros=settings.NUMEROS_LINHAS)

    for indice_linha, letra in enumerate(settings.LETRAS_COLUNAS):
        for indice_coluna, numero in enumerate(settings.NUMEROS_LINHAS):
            cor = cor_da_celula(placa, indice_linha, indice_coluna,
                                ALTURA_CELULA, LARGURA_CELULA)
            assert cor == _cor_unica(letra, numero), (
                f"celula {letra}{numero} saiu na posicao errada "
                f"(linha {indice_linha}, coluna {indice_coluna})"
            )


def test_letras_sao_linhas_e_numeros_sao_colunas():
    """Blindagem contra a placa sair transposta."""
    placa = montar_placa(_grid_completo(),
                         ordem_letras=settings.LETRAS_COLUNAS,
                         ordem_numeros=settings.NUMEROS_LINHAS)
    # a1 e a10 estao na MESMA faixa horizontal (mesma linha, colunas 0 e 9)
    a1 = cor_da_celula(placa, 0, 0, ALTURA_CELULA, LARGURA_CELULA)
    a10 = cor_da_celula(placa, 0, 9, ALTURA_CELULA, LARGURA_CELULA)
    assert a1 == _cor_unica("a", 1)
    assert a10 == _cor_unica("a", 10)
    # a1 e d1 estao na MESMA coluna, em faixas diferentes
    d1 = cor_da_celula(placa, 3, 0, ALTURA_CELULA, LARGURA_CELULA)
    assert d1 == _cor_unica("d", 1)


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------


def test_celula_ausente_vira_placeholder_amarelo():
    quadrantes = _grid_completo()
    del quadrantes[("c", 7)]

    placa = montar_placa(quadrantes,
                         ordem_letras=settings.LETRAS_COLUNAS,
                         ordem_numeros=settings.NUMEROS_LINHAS,
                         cor_placeholder=settings.STITCHING_COR_PLACEHOLDER)

    cor = cor_da_celula(placa, 2, 6, ALTURA_CELULA, LARGURA_CELULA)
    assert cor == settings.STITCHING_COR_PLACEHOLDER == (40, 230, 250)


def test_celulas_faltantes_sao_listadas():
    quadrantes = _grid_completo()
    del quadrantes[("a", 1)]
    del quadrantes[("d", 10)]
    faltantes = celulas_faltantes(quadrantes)
    assert faltantes == ["a1", "d10"]


def test_montar_placa_sem_quadrantes_levanta():
    with pytest.raises(ValueError):
        montar_placa({})


# ---------------------------------------------------------------------------
# Carregamento e normalizacao
# ---------------------------------------------------------------------------


def test_carregar_quadrantes_le_nomes_letra_numero(tmp_path, quadrante_pequeno):
    dados = quadrante_pequeno.read_bytes()
    for nome in ("a1.png", "b3.png", "d10.png"):
        (tmp_path / nome).write_bytes(dados)
    (tmp_path / "leiame.txt").write_text("ignorar", encoding="utf-8")

    quadrantes = carregar_quadrantes(tmp_path,
                                     padrao_nome=settings.STITCHING_PADRAO_NOME)

    assert set(quadrantes) == {("a", 1), ("b", 3), ("d", 10)}


def test_escala_1_nao_altera_pixels(tmp_path, quadrante_pequeno):
    (tmp_path / "a1.png").write_bytes(quadrante_pequeno.read_bytes())
    quadrantes = carregar_quadrantes(tmp_path, escala=1.0)
    assert np.array_equal(quadrantes[("a", 1)], cv2.imread(str(quadrante_pequeno)))


def test_normalizar_usa_a_mediana_dos_tamanhos():
    quadrantes = {
        ("a", 1): criar_quadrante((10, 10, 10), 20, 12),
        ("a", 2): criar_quadrante((20, 20, 20), 20, 12),
        ("a", 3): criar_quadrante((30, 30, 30), 26, 18),  # fora do padrao
    }
    normalizados = normalizar_tamanhos(quadrantes)
    assert {img.shape[:2] for img in normalizados.values()} == {(12, 20)}


def test_executar_stitching_ponta_a_ponta(tmp_path, quadrante_pequeno):
    """Fluxo real: le os arquivos da pasta e monta a placa 4x10."""
    dados = quadrante_pequeno.read_bytes()
    for letra in settings.LETRAS_COLUNAS:
        for numero in settings.NUMEROS_LINHAS:
            (tmp_path / f"{letra}{numero}.png").write_bytes(dados)

    placa, estatisticas = executar_stitching(pasta_imagens=tmp_path)

    assert estatisticas["quadrantes_carregados"] == settings.QUANTIDADE_ESPERADA
    assert estatisticas["placeholders"] == 0
    altura_celula, largura_celula = estatisticas["tamanho_celula"]
    assert placa.shape[:2] == (4 * altura_celula, 10 * largura_celula)


def test_stitching_com_lote_incompleto_preenche_placeholder(tmp_path,
                                                            quadrante_pequeno):
    dados = quadrante_pequeno.read_bytes()
    for letra in settings.LETRAS_COLUNAS:
        for numero in settings.NUMEROS_LINHAS:
            if (letra, numero) in {("b", 5), ("d", 2)}:
                continue
            (tmp_path / f"{letra}{numero}.png").write_bytes(dados)

    placa, estatisticas = executar_stitching(pasta_imagens=tmp_path)

    assert estatisticas["placeholders"] == 2
    assert sorted(estatisticas["celulas_faltantes"]) == ["b5", "d2"]
    altura_celula, largura_celula = estatisticas["tamanho_celula"]
    assert cor_da_celula(placa, 1, 4, altura_celula, largura_celula) == (40, 230, 250)


# ---------------------------------------------------------------------------
# Exportacao
# ---------------------------------------------------------------------------


def test_exportacao_nao_faz_upscale(tmp_path):
    """Resolucoes maiores que a placa sao ignoradas."""
    placa = np.full((100, 1000, 3), (10, 20, 30), dtype=np.uint8)
    gerados = exportar_multiplas_resolucoes(
        placa, str(tmp_path), settings.EXPORTACAO_RESOLUCOES,
        incluir_png_lossless=False, incluir_tiff=False, incluir_webp=False,
    )
    # placa com 1000 px de largura: so 720p cabe; 10k, 4k e 1200p sao puladas
    assert gerados == ["placa_720p.jpg"]


def test_exportacao_gera_todos_os_formatos(tmp_path):
    placa = np.full((100, 1000, 3), (10, 20, 30), dtype=np.uint8)
    gerados = exportar_multiplas_resolucoes(
        placa, str(tmp_path), settings.EXPORTACAO_RESOLUCOES,
        incluir_png_lossless=True, incluir_tiff=True, incluir_webp=True,
    )
    assert set(gerados) == {"placa_720p.jpg", "placa_LOSSLESS.png",
                            "placa_CIENTIFICO.tiff", "placa_WEBP.webp"}
    for nome in gerados:
        assert (tmp_path / nome).stat().st_size > 0


def test_png_e_tiff_exportados_sao_lossless(tmp_path):
    """A placa em PNG/TIFF tem que voltar identica do disco."""
    rng = np.random.default_rng(seed=42)
    placa = rng.integers(0, 256, size=(80, 900, 3), dtype=np.uint8)

    exportar_multiplas_resolucoes(placa, str(tmp_path), [],
                                  incluir_png_lossless=True,
                                  incluir_tiff=True, incluir_webp=False)

    assert np.array_equal(cv2.imread(str(tmp_path / "placa_LOSSLESS.png")), placa)
    assert np.array_equal(cv2.imread(str(tmp_path / "placa_CIENTIFICO.tiff")), placa)


def test_parametros_de_stitching_preservados():
    assert settings.STITCHING_ESCALA_CARREGAMENTO == 1.0
    assert settings.STITCHING_COR_PLACEHOLDER == (40, 230, 250)
    assert settings.LETRAS_COLUNAS == ["a", "b", "c", "d"]
    assert settings.NUMEROS_LINHAS == list(range(1, 11))
    assert settings.QUANTIDADE_ESPERADA == 40
    assert settings.EXPORTACAO_RESOLUCOES == [
        ("10k", 10000, 92), ("4k", 3840, 92),
        ("1200p", 1200, 90), ("720p", 720, 88),
    ]
