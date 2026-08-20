"""
Regressao do recorte contra o ACERVO REAL de fotos.

As fixtures sinteticas provam que a logica esta certa; so o acervo prova que
a CALIBRACAO esta certa. Estes testes rodam sobre as fotos de verdade e sao
pulados quando as pastas nao estao no disco (maquina de CI, clone limpo).

    pytest tests/test_recorte_acervo.py -m acervo

O criterio nao e "achou alguma coisa", e o que importa para o proximo
estagio do sistema:

  1. cobertura   - toda foto de armadilha tem que virar quadrante;
  2. limpeza     - nenhuma linha da grade pode sobrar DENTRO do quadrante;
  3. consistencia- o mesmo quadrante fotografado varias vezes tem que sair
                   sempre do mesmo tamanho (e a metrica que denunciou o bug
                   do azul: a largura variava de 0.47 a 0.95 da foto).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from config import settings
from src import recorte as R

pytestmark = [pytest.mark.acervo, pytest.mark.lento]

# (nome, pasta, perfil, largura mediana esperada, desvio maximo tolerado)
ACERVOS = [
    ("azul", settings.PASTA_DADOS / "azuis" / "BlueTrap", "azul", 0.457, 0.02),
    ("amarela", settings.PASTA_ENTRADA, "amarela", 0.535, 0.02),
]

# Fotos que NAO sao de armadilha (fora de foco, sem grade). Elas tem que ser
# recusadas, entao nao entram na conta de cobertura.
NAO_SAO_ARMADILHA = {"20241126_205023.JPG", "20241126_205222.JPG"}


def _fotos(pasta):
    if not pasta.is_dir():
        return []
    return sorted(c for c in pasta.iterdir()
                  if c.suffix.lower() in settings.EXTENSOES_IMAGEM)


def _reduzida(caminho):
    imagem = cv2.imread(str(caminho))
    fator = settings.RECORTE_FATOR_DETECCAO
    reduzida = cv2.resize(imagem, None, fx=fator, fy=fator,
                          interpolation=cv2.INTER_AREA)
    del imagem
    return reduzida


def _linhas_no_miolo(imagem, borda_frac=0.03, forca_min=0.55):
    """Picos fortes de linha de grade no MIOLO do recorte, nos dois eixos."""
    canal = cv2.medianBlur(cv2.cvtColor(imagem, cv2.COLOR_BGR2HSV)[..., 2], 5)
    achados = []
    for eixo, dado in (("x", canal), ("y", np.ascontiguousarray(canal.T))):
        extensao = dado.shape[1]
        mascara = R._mascara_linhas(
            dado, R._impar(extensao * settings.RECORTE_ESPESSURA_MAX_FRAC, 15))
        tangente, _ = R._estimar_inclinacao(
            mascara, settings.RECORTE_INCLINACAO_MAX_GRAUS)
        perfil = R._perfil_colunas(R._cisalhar(mascara, tangente), 0.55,
                                   settings.RECORTE_PONTE_FRAC)
        margem = extensao * borda_frac
        achados += [(eixo, round(p[0]))
                    for p in R._picos(perfil, max(10, int(extensao * 0.02)),
                                      forca_min)
                    if margem < p[0] < extensao - margem]
    return achados


@pytest.fixture(scope="module")
def resultados():
    """Roda a deteccao uma vez por acervo e reaproveita nos tres testes."""
    saida = {}
    for nome, pasta, perfil, _, _ in ACERVOS:
        fotos = _fotos(pasta)
        if not fotos:
            continue
        saida[nome] = [(caminho.name, R.detectar_crop_box(_reduzida(caminho)))
                       for caminho in fotos]
    if not saida:
        pytest.skip("acervo de fotos reais nao esta neste disco")
    return saida


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_toda_foto_de_armadilha_vira_quadrante(nome, pasta, perfil, largura,
                                               desvio, resultados):
    if nome not in resultados:
        pytest.skip(f"acervo '{nome}' nao esta neste disco")
    falhas = [arquivo for arquivo, info in resultados[nome]
              if not info["sucesso"] and arquivo not in NAO_SAO_ARMADILHA]
    assert not falhas, f"sem quadrante: {falhas}"


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_largura_do_quadrante_e_consistente(nome, pasta, perfil, largura,
                                            desvio, resultados):
    """
    A metrica que denunciou o bug do azul. Fotos do mesmo microscopio sobre
    a mesma armadilha tem que entregar quadrantes do mesmo tamanho; o
    algoritmo antigo variava de 0.47 a 0.95 da largura da foto.
    """
    if nome not in resultados:
        pytest.skip(f"acervo '{nome}' nao esta neste disco")
    larguras = [(x2 - x1) / info["dim_deteccao"][1]
                for _, info in resultados[nome] if info["sucesso"]
                for _, _, x1, x2 in [info["crop_box_px"]]]
    assert np.median(larguras) == pytest.approx(largura, abs=desvio)
    assert np.std(larguras) < 0.02, f"larguras dispersas: {sorted(larguras)[:5]}"


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_nenhuma_linha_da_grade_sobra_no_quadrante(nome, pasta, perfil,
                                                   largura, desvio):
    """
    O criterio final de qualidade: o que sai nao pode ter linha de grade no
    miolo. Antes desta calibracao, so 7 das 65 fotos azuis passavam aqui.
    """
    fotos = _fotos(pasta)
    if not fotos:
        pytest.skip(f"acervo '{nome}' nao esta neste disco")
    sujos = []
    for caminho in fotos:
        reduzida = _reduzida(caminho)
        y1, y2, x1, x2 = R.detectar_crop_box(reduzida)["crop_box_px"]
        achados = _linhas_no_miolo(reduzida[y1:y2, x1:x2])
        if achados:
            sujos.append((caminho.name, achados))
    assert not sujos, f"linha de grade dentro do quadrante: {sujos}"
