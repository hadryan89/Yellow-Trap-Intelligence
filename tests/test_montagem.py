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
from src.exportacao import compor_quadro, exportar_placa
from src.montagem import (
    altura_maxima_celula,
    altura_separador,
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


def test_armadilha_inteira_rende_uma_placa_com_frente_e_verso():
    caminhos = [Path(f"{n}.png") for n in _nomes_grid("ABCDEFGH")]
    placas = planejar_placas(caminhos)

    assert [p.nome for p in placas] == ["VARD14A1-VARD14H10"]
    assert len(placas[0].celulas) == 80
    assert placas[0].celulas[39].stem == "VARD14D10"  # fim da frente
    assert placas[0].celulas[40].stem == "VARD14E1"   # comeco do verso


def test_ultima_placa_incompleta_mantem_o_formato():
    caminhos = [Path(f"VARD{i}.png") for i in range(1, 86)]
    placas = planejar_placas(caminhos)

    assert [p.nome for p in placas] == ["VARD1-VARD80", "VARD81-VARD85"]
    assert len(placas[1].celulas) == 80
    assert placas[1].quadrantes_esperados == 5
    assert placas[1].celulas[5:] == [None] * 75


def test_listar_quadrantes_em_ordem_natural(tmp_path):
    _gravar_quadrantes(tmp_path, ["VARD10", "VARD2", "VARD1"])
    (tmp_path / "notas.txt").write_text("x")

    assert [p.stem for p in listar_quadrantes(tmp_path)] == ["VARD1", "VARD2", "VARD10"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_layout_frente_em_cima_verso_embaixo(tmp_path):
    """A..D (frente) em cima, separador, E..H (verso) embaixo - 4 faixas de 10 cada."""
    caminhos = _gravar_quadrantes(tmp_path, _nomes_grid("ABCDEFGH"))
    plano = planejar_placas(caminhos)[0]

    placa, estatisticas = montar_placa_do_plano(plano)

    separador = altura_separador(12)
    assert placa.shape == (8 * 12 + separador, 10 * 20, 3)
    assert estatisticas["placeholders"] == 0
    for i in range(80):
        linha, coluna = divmod(i, 10)
        y = linha * 12 + (separador if linha >= 4 else 0)
        assert tuple(placa[y + 5, coluna * 20 + 9]) == _cor(i), i
    faixa_separadora = placa[4 * 12:4 * 12 + separador]
    assert (faixa_separadora == settings.MONTAGEM_COR_SEPARADOR).all()


def test_lote_so_com_a_frente_deixa_o_verso_em_placeholder(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, _nomes_grid("ABCD"))
    placa, estatisticas = montar_placa_do_plano(planejar_placas(caminhos)[0])

    assert estatisticas["placeholders"] == 40
    verso = placa[4 * 12 + altura_separador(12):]
    assert (verso == PLACEHOLDER).all()


def test_celula_sem_quadrante_vira_placeholder(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, _nomes_grid())
    caminhos[12].unlink()  # B3
    plano = planejar_placas(caminhos)[0]

    placa, estatisticas = montar_placa_do_plano(plano)

    assert estatisticas["faltantes"] == ["VARD14B3"]
    assert estatisticas["placeholders"] == 41  # B3 + o verso inteiro
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


def test_quadrante_grande_e_reduzido_ao_carregar(tmp_path):
    caminhos = _gravar_quadrantes(tmp_path, ["VARD1"], largura=40, altura=24)
    _, estatisticas = montar_placa_do_plano(planejar_placas(caminhos)[0],
                                            altura_max=12)
    assert estatisticas["tamanho_celula"] == [12, 20]


def test_altura_maxima_da_celula_segue_o_quadro_final():
    """80 celulas = 8 faixas: 1080 / 8 = 135 px no quadro, com folga de 2x."""
    assert altura_maxima_celula(80, colunas=10) == 2 * 135


# ---------------------------------------------------------------------------
# Exportacao
# ---------------------------------------------------------------------------


def test_exportar_placa_grava_um_unico_png_1920x1080(tmp_path):
    placa = criar_quadrante((10, 20, 30), largura=200, altura=160)

    arquivos = exportar_placa(placa, tmp_path / "saida", "VARD14A1-VARD14H10")

    assert arquivos == ["VARD14A1-VARD14H10.png"]
    assert [p.name for p in (tmp_path / "saida").iterdir()] == arquivos
    quadro = cv2.imread(str(tmp_path / "saida" / arquivos[0]))
    assert quadro.shape == (1080, 1920, 3)


def test_quadro_encaixa_a_placa_sem_distorcer():
    """Placa 200 x 160 (5:4) no quadro 1920 x 1080: 1350 x 1080, centralizada."""
    placa = criar_quadrante((10, 20, 30), largura=200, altura=160)

    quadro = compor_quadro(placa, rotulos=())

    assert tuple(quadro[540, 960]) == (10, 20, 30)
    assert tuple(quadro[540, 285 + 5]) == (10, 20, 30)
    assert tuple(quadro[540, 285 - 5]) == settings.EXPORTACAO_COR_FUNDO
    assert tuple(quadro[540, 1920 - 285 + 5]) == settings.EXPORTACAO_COR_FUNDO


def test_quadro_rotula_frente_e_verso_na_margem():
    placa = criar_quadrante((10, 20, 30), largura=200, altura=160)

    quadro = compor_quadro(placa)

    margem = quadro[:, :285]
    rotulo = np.all(margem == settings.EXPORTACAO_COR_ROTULO, axis=2)
    linhas = np.flatnonzero(rotulo.any(axis=1))
    assert linhas.size, "nenhum rotulo desenhado"
    assert linhas.min() < 540 < linhas.max()  # um rotulo em cada metade
    assert (linhas < 540).any() and (linhas > 540).any()


def test_margem_estreita_omite_os_rotulos():
    placa = criar_quadrante((10, 20, 30), largura=1920, altura=1080)
    quadro = compor_quadro(placa)
    assert (quadro == (10, 20, 30)).all()
