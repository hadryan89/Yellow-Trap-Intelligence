"""
Testes da etapa 3: montagem da placa e exportacao.

Usam quadrantes solidos de cor conhecida, entao da para conferir pixel a
pixel onde cada um foi parar.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from config import settings
from src.exportacao import exportar_placa
from src.montagem import (
    listar_quadrantes,
    montar_placa,
    montar_placa_do_plano,
    normalizar_tamanhos,
    planejar_placas,
)
from tests.conftest import criar_quadrante

PLACEHOLDER = settings.MONTAGEM_COR_PLACEHOLDER


def _cor(i: int) -> tuple[int, int, int]:
    """Cor unica (e diferente do placeholder) para o quadrante i."""
    return (i * 5 % 256, 255 - i * 3, 17 + i)


def _gravar_quadrantes(pasta: Path, nomes, largura=20, altura=12) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for i, nome in enumerate(nomes):
        caminho = pasta / f"{nome}.png"
        cv2.imwrite(str(caminho), criar_quadrante(_cor(i), largura, altura))
        caminhos.append(caminho)
    return caminhos


def _nomes_grid(letras="ABCD", armadilha=14):
    return [f"VARD{armadilha}{letra}{n}" for letra in letras for n in range(1, 11)]


# ---------------------------------------------------------------------------
# Plano das placas
# ---------------------------------------------------------------------------


def test_armadilha_inteira_rende_uma_placa_por_lado():
    caminhos = [Path(f"{n}.png") for n in _nomes_grid("ABCDEFGH")]
    placas = planejar_placas(caminhos)

    assert [p.nome for p in placas] == ["VARD14A1-VARD14D10", "VARD14E1-VARD14H10"]
    assert all(len(p.celulas) == 40 for p in placas)
    assert placas[1].celulas[0].stem == "VARD14E1"


def test_ultima_placa_incompleta_mantem_o_formato():
    caminhos = [Path(f"VARD{i}.png") for i in range(1, 46)]
    placas = planejar_placas(caminhos)

    assert [p.nome for p in placas] == ["VARD1-VARD40", "VARD41-VARD45"]
    assert len(placas[1].celulas) == 40
    assert placas[1].quadrantes_esperados == 5
    assert placas[1].celulas[5:] == [None] * 35


def test_listar_quadrantes_em_ordem_natural(tmp_path):
    _gravar_quadrantes(tmp_path, ["VARD10", "VARD2", "VARD1"])
    (tmp_path / "notas.txt").write_text("x")

    assert [p.stem for p in listar_quadrantes(tmp_path)] == ["VARD1", "VARD2", "VARD10"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_layout_horizontal_quatro_faixas_de_dez(tmp_path):
    """A1..A10 e a faixa de cima, D1..D10 a de baixo."""
    caminhos = _gravar_quadrantes(tmp_path, _nomes_grid())
    plano = planejar_placas(caminhos)[0]

    placa, estatisticas = montar_placa_do_plano(plano)

    assert placa.shape == (4 * 12, 10 * 20, 3)
    assert estatisticas["placeholders"] == 0
    for i in range(40):
        linha, coluna = divmod(i, 10)
        assert tuple(placa[linha * 12 + 5, coluna * 20 + 9]) == _cor(i), i


def test_celula_sem_quadrante_vira_placeholder(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, _nomes_grid())
    caminhos[12].unlink()  # B3
    plano = planejar_placas(caminhos)[0]

    placa, estatisticas = montar_placa_do_plano(plano)

    assert estatisticas["faltantes"] == ["VARD14B3"]
    assert estatisticas["placeholders"] == 1
    assert (placa[12:24, 40:60] == PLACEHOLDER).all()
    assert tuple(placa[12 + 5, 3 * 20 + 9]) == _cor(13)  # B4 no lugar dele


def test_quadrante_ilegivel_vira_placeholder(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, ["VARD1", "VARD2"])
    caminhos[1].write_bytes(b"corrompido")

    _, estatisticas = montar_placa_do_plano(planejar_placas(caminhos)[0])

    assert estatisticas["faltantes"] == ["VARD2"]
    assert estatisticas["quadrantes"] == 1


def test_placa_sem_nenhum_quadrante_levanta(tmp_path):
    plano = planejar_placas([tmp_path / "VARD1.png"])[0]
    with pytest.raises(ValueError, match="nenhum quadrante"):
        montar_placa_do_plano(plano)


def test_tamanhos_diferentes_sao_normalizados_pela_mediana():
    quadrantes = {
        0: criar_quadrante((1, 2, 3), largura=20, altura=12),
        1: criar_quadrante((1, 2, 3), largura=20, altura=12),
        2: criar_quadrante((1, 2, 3), largura=26, altura=15),
    }
    normalizados = normalizar_tamanhos(quadrantes)
    assert {img.shape[:2] for img in normalizados.values()} == {(12, 20)}


def test_montagem_identica_ao_hstack_vstack_do_colab():
    """O preenchimento pre-alocado reproduz o hstack + vstack original."""
    rng = np.random.default_rng(7)
    quadrantes = {i: rng.integers(0, 256, (12, 20, 3), dtype=np.uint8)
                  for i in range(40) if i not in (7, 33)}

    faixas = []
    for linha in range(4):
        celulas = [quadrantes.get(linha * 10 + c,
                                  np.full((12, 20, 3), PLACEHOLDER, dtype=np.uint8))
                   for c in range(10)]
        faixas.append(np.hstack(celulas))
    esperado = np.vstack(faixas)

    placa = montar_placa(dict(quadrantes), total_celulas=40, colunas=10)
    assert np.array_equal(placa, esperado)


def test_escala_reduz_a_placa(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, ["VARD1"], largura=40, altura=24)
    placa, _ = montar_placa_do_plano(planejar_placas(caminhos)[0], escala=0.5)
    assert placa.shape[:2] == (4 * 12, 10 * 20)


# ---------------------------------------------------------------------------
# Exportacao
# ---------------------------------------------------------------------------


def test_exportar_placa_grava_todos_os_formatos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EXPORTACAO_RESOLUCOES",
                        [("grande", 9999, 92), ("pequena", 100, 90)])
    placa = criar_quadrante((10, 20, 30), largura=200, altura=80)

    arquivos = exportar_placa(placa, tmp_path / "saida", "VARD1-VARD40")

    # 'grande' passa da largura da placa: sem upscale, nao e gerada.
    assert arquivos == ["VARD1-VARD40_pequena.jpg", "VARD1-VARD40_LOSSLESS.png",
                        "VARD1-VARD40_CIENTIFICO.tiff", "VARD1-VARD40_WEBP.webp"]
    pequena = cv2.imread(str(tmp_path / "saida" / "VARD1-VARD40_pequena.jpg"))
    assert pequena.shape[:2] == (40, 100)
    lossless = cv2.imread(str(tmp_path / "saida" / "VARD1-VARD40_LOSSLESS.png"))
    assert np.array_equal(lossless, placa)


def test_exportar_placa_nao_apaga_as_outras_placas(tmp_path):
    placa = criar_quadrante((10, 20, 30), largura=40, altura=16)
    exportar_placa(placa, tmp_path, "VARD14A1-VARD14D10")
    exportar_placa(placa, tmp_path, "VARD14E1-VARD14H10")

    nomes = {p.name for p in tmp_path.iterdir()}
    assert "VARD14A1-VARD14D10_LOSSLESS.png" in nomes
    assert "VARD14E1-VARD14H10_LOSSLESS.png" in nomes


def test_webp_acima_do_limite_sai_reduzido(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EXPORTACAO_WEBP_LADO_MAXIMO", 50)
    placa = criar_quadrante((10, 20, 30), largura=200, altura=80)

    exportar_placa(placa, tmp_path, "P", resolucoes=[],
                   incluir_png_lossless=False, incluir_tiff=False)

    webp = cv2.imread(str(tmp_path / "P_WEBP.webp"))
    assert webp.shape[:2] == (20, 50)
