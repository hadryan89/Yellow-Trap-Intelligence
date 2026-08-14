"""
Testes do protocolo 2 (recorte).

O objetivo NAO e testar "se o codigo roda", e sim travar o comportamento
validado no Colab:

  * os parametros de calibracao nao podem mudar;
  * a deteccao roda na copia reduzida, mas o crop sai na resolucao cheia;
  * o crop e uma fatia exata da imagem original (zero reprocessamento de pixel);
  * deteccao falha -> devolve a imagem cheia (comportamento do Colab).
"""

from __future__ import annotations

import inspect
import json

import cv2
import numpy as np
import pytest

from config import settings
from src import recorte
from src.recorte import detectar_crop_box, processar_foto, recortar_em_resolucao_cheia
from tests.fixtures.gerar_fixtures import (
    ALTURA_VALIDA,
    CROP_ESPERADO_X,
    LARGURA_VALIDA,
)


# ---------------------------------------------------------------------------
# Calibracao
# ---------------------------------------------------------------------------


def test_parametros_de_calibracao_preservados():
    """Os valores calibrados no Colab nao podem ter mudado em settings.py."""
    assert settings.RECORTE_FATOR_DETECCAO == 0.25
    assert settings.RECORTE_MARGEM == 8
    assert settings.RECORTE_MODO == "so_vertical"
    assert settings.RECORTE_SPAN_V_INICIAL_FRAC == 0.5
    assert settings.RECORTE_SPAN_H_INICIAL_FRAC == 0.5
    assert settings.RECORTE_SPAN_MINIMO_FRAC == 0.15
    assert settings.RECORTE_DILATAR is True


def test_defaults_das_funcoes_iguais_ao_colab():
    """As assinaturas migradas mantem os mesmos defaults do notebook."""
    defaults_detectar = {
        nome: p.default
        for nome, p in inspect.signature(detectar_crop_box).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    assert defaults_detectar == {
        "margem": 8,
        "modo": "so_vertical",
        "span_v_inicial_frac": 0.5,
        "span_h_inicial_frac": 0.5,
        "span_minimo_frac": 0.15,
        "dilatar": True,
    }

    defaults_recortar = {
        nome: p.default
        for nome, p in inspect.signature(recortar_em_resolucao_cheia).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    assert defaults_recortar == {
        "fator_deteccao": 0.25,
        "margem": 8,
        "modo": "so_vertical",
        "span_v_inicial_frac": 0.5,
        "span_h_inicial_frac": 0.5,
        "span_minimo_frac": 0.15,
        "dilatar": True,
    }


def test_defaults_do_detector_de_linhas():
    """limiar_frac=0.25 e min_dist=30 fazem parte da calibracao."""
    parametros = inspect.signature(recorte._detectar_linhas_em_eixo).parameters
    assert parametros["limiar_frac"].default == 0.25
    assert parametros["min_dist"].default == 30


# ---------------------------------------------------------------------------
# Deteccao
# ---------------------------------------------------------------------------


def test_deteccao_encontra_as_quatro_linhas(foto_valida):
    """As 4 linhas verticais da grade devem virar 4 picos."""
    imagem = cv2.imread(str(foto_valida))
    reduzida = cv2.resize(imagem, None, fx=0.25, fy=0.25,
                          interpolation=cv2.INTER_AREA)
    info = detectar_crop_box(reduzida)

    assert info["sucesso"] is True
    assert len(info["picos_v"]) == 4
    assert info["picos_h"] == []  # modo so_vertical nao procura horizontais


def test_crop_fica_dentro_do_quadrante_central(foto_valida):
    """
    O recorte tem que cair ENTRE as duas linhas centrais (x=400 e x=800),
    e nao entre as linhas dos quadrantes vizinhos.
    """
    _, info = recortar_em_resolucao_cheia(str(foto_valida))
    y1, y2, x1, x2 = info["crop_box_cheia_px"]
    esquerda, direita = CROP_ESPERADO_X

    assert esquerda < x1 < esquerda + 60, "borda esquerda fora do quadrante central"
    assert direita - 60 < x2 < direita, "borda direita fora do quadrante central"
    # margem=8 e aplicada na escala da deteccao (1 px reduzido = 4 px cheios)
    assert x1 - esquerda >= settings.RECORTE_MARGEM


def test_modo_so_vertical_nao_recorta_na_horizontal(foto_valida):
    """Com modo='so_vertical' a altura tem que sair inteira."""
    _, info = recortar_em_resolucao_cheia(str(foto_valida))
    y1, y2, _, _ = info["crop_box_cheia_px"]
    assert (y1, y2) == (0, ALTURA_VALIDA)


def test_deteccao_usa_copia_reduzida_e_crop_sai_em_resolucao_cheia(foto_valida):
    """
    A deteccao roda em 25% do tamanho; o crop e aplicado na imagem original.
    """
    recortada, info = recortar_em_resolucao_cheia(str(foto_valida))

    altura_det, largura_det = info["dim_deteccao"]
    assert (largura_det, altura_det) == (
        int(LARGURA_VALIDA * 0.25), int(ALTURA_VALIDA * 0.25),
    )
    assert info["dim_original"] == (ALTURA_VALIDA, LARGURA_VALIDA)
    # Altura cheia preservada e largura bem maior que a da deteccao:
    assert recortada.shape[0] == ALTURA_VALIDA
    assert recortada.shape[1] > largura_det


def test_crop_e_fatia_exata_da_original(foto_valida):
    """
    Nenhum pixel pode ser reamostrado: a saida tem que ser identica a fatia
    correspondente da imagem original.
    """
    original = cv2.imread(str(foto_valida))
    recortada, info = recortar_em_resolucao_cheia(str(foto_valida))
    y1, y2, x1, x2 = info["crop_box_cheia_px"]

    assert np.array_equal(recortada, original[y1:y2, x1:x2])


def test_deteccao_e_deterministica(foto_valida):
    """Duas execucoes seguidas tem que dar exatamente o mesmo crop box."""
    _, info_a = recortar_em_resolucao_cheia(str(foto_valida))
    _, info_b = recortar_em_resolucao_cheia(str(foto_valida))
    assert info_a["crop_box_cheia_px"] == info_b["crop_box_cheia_px"]
    assert info_a["picos_v"] == info_b["picos_v"]


def test_falha_de_deteccao_devolve_imagem_cheia(foto_sem_grade):
    """Comportamento validado no Colab: sem linhas -> imagem inteira."""
    imagem_original = cv2.imread(str(foto_sem_grade))
    devolvida, info = recortar_em_resolucao_cheia(str(foto_sem_grade))

    assert info["sucesso"] is False
    assert np.array_equal(devolvida, imagem_original)


def test_imagem_ilegivel_devolve_none(tmp_path):
    """Arquivo que nao e imagem nao pode explodir - devolve (None, None)."""
    falso = tmp_path / "nao_e_imagem.jpg"
    falso.write_bytes(b"isto nao e um jpeg")
    imagem, info = recortar_em_resolucao_cheia(str(falso))
    assert imagem is None and info is None


# ---------------------------------------------------------------------------
# Worker (processar_foto)
# ---------------------------------------------------------------------------


def test_processar_foto_salva_quadrante_lossless(foto_valida, pastas_isoladas):
    """O PNG gravado tem que ser bit-a-bit igual ao array recortado."""
    saida = pastas_isoladas["PASTA_RECORTADAS"]
    resultado = processar_foto(str(foto_valida), pasta_saida=saida, formato="png")

    assert resultado["sucesso"] is True
    assert resultado["deteccao_ok"] is True

    caminho = saida / "foto_grade_valida.png"
    assert caminho.exists()

    esperado, _ = recortar_em_resolucao_cheia(str(foto_valida))
    assert np.array_equal(cv2.imread(str(caminho)), esperado)


@pytest.mark.parametrize("formato,extensao", [("png", ".png"), ("tiff", ".tiff"),
                                              ("jpg_max", ".jpg")])
def test_processar_foto_respeita_o_formato(foto_valida, pastas_isoladas,
                                           formato, extensao):
    saida = pastas_isoladas["PASTA_RECORTADAS"]
    resultado = processar_foto(str(foto_valida), pasta_saida=saida, formato=formato)
    assert resultado["sucesso"] is True
    assert resultado["saida"].endswith(extensao)


def test_tiff_tambem_e_lossless(foto_valida, pastas_isoladas):
    """TIFF LZW precisa preservar os pixels exatamente."""
    saida = pastas_isoladas["PASTA_RECORTADAS"]
    processar_foto(str(foto_valida), pasta_saida=saida, formato="tiff")
    esperado, _ = recortar_em_resolucao_cheia(str(foto_valida))
    gravado = cv2.imread(str(saida / "foto_grade_valida.tiff"))
    assert np.array_equal(gravado, esperado)


def test_sem_deteccao_salva_imagem_cheia_e_registra_o_motivo(
    foto_sem_grade, pastas_isoladas, monkeypatch
):
    """
    Default (RECORTE_FALHA_DETECCAO_E_ERRO=False): o quadrante e salvo com a
    imagem cheia, a foto NAO e movida e um JSON de diagnostico e gravado.
    """
    monkeypatch.setattr(settings, "RECORTE_FALHA_DETECCAO_E_ERRO", False)
    saida = pastas_isoladas["PASTA_RECORTADAS"]

    resultado = processar_foto(str(foto_sem_grade), pasta_saida=saida,
                               formato="png", lote_id="LOTE_TESTE")

    assert resultado["sucesso"] is True
    assert resultado["deteccao_ok"] is False
    assert foto_sem_grade.exists(), "a foto original nao pode ser movida"

    gravado = cv2.imread(str(saida / "foto_sem_grade.png"))
    assert np.array_equal(gravado, cv2.imread(str(foto_sem_grade)))

    jsons = list((pastas_isoladas["PASTA_FALHAS"] / "LOTE_TESTE").glob("*.json"))
    assert len(jsons) == 1
    registro = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert registro["etapa"] == "recorte_sem_deteccao"
    assert "Deteccao" in registro["motivo"]


def test_modo_estrito_move_para_falhas(foto_sem_grade, pastas_isoladas,
                                       monkeypatch, tmp_path):
    """
    Com RECORTE_FALHA_DETECCAO_E_ERRO=True o quadrante nao e gerado e a foto
    vai para _falhas (o stitching preenche a celula com o placeholder).
    """
    monkeypatch.setattr(settings, "RECORTE_FALHA_DETECCAO_E_ERRO", True)
    monkeypatch.setattr(settings, "FALHAS_COPIAR_EM_VEZ_DE_MOVER", False)

    copia = tmp_path / "copia_sem_grade.png"
    copia.write_bytes(foto_sem_grade.read_bytes())
    saida = pastas_isoladas["PASTA_RECORTADAS"]

    resultado = processar_foto(str(copia), pasta_saida=saida, lote_id="LOTE_ESTRITO")

    assert resultado["sucesso"] is False
    assert not (saida / "copia_sem_grade.png").exists()
    assert not copia.exists(), "no modo estrito a foto e movida para _falhas"
    assert (pastas_isoladas["PASTA_FALHAS"] / "LOTE_ESTRITO"
            / "copia_sem_grade.png").exists()


def test_worker_nunca_levanta_excecao(tmp_path, pastas_isoladas):
    """Uma foto corrompida vira 'erro' no resultado, sem derrubar o lote."""
    corrompida = tmp_path / "corrompida.jpg"
    corrompida.write_bytes(b"\xff\xd8\xff\xe0lixo")

    resultado = processar_foto(str(corrompida),
                               pasta_saida=pastas_isoladas["PASTA_RECORTADAS"])

    assert resultado["sucesso"] is False
    assert resultado["erro"]
    assert resultado["arquivo"] == "corrompida.jpg"
