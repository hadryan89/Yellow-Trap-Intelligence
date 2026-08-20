"""
Testes do protocolo 2 (recorte).

O objetivo NAO e testar "se o codigo roda", e sim travar o comportamento que
foi medido no acervo real:

  * os parametros de calibracao nao podem mudar sem alguem perceber;
  * o detector e CEGO A COR: armadilha amarela e azul, mesma geometria,
    tem que sair com o mesmo crop box;
  * a linha da grade nunca entra no quadrante entregue;
  * o recorte acontece nos quatro lados (a celula e menor que a foto);
  * a inclinacao da grade e corrigida;
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
from src.recorte import (
    detectar_crop_box,
    processar_foto,
    processar_item,
    recortar_em_resolucao_cheia,
    resolver_perfil,
)
from tests.fixtures.gerar_fixtures import (
    ALTURA_VALIDA,
    CROP_ESPERADO_X,
    CROP_ESPERADO_Y,
    ESPESSURA_LINHA,
    INCLINACAO_GRAUS,
    LARGURA_VALIDA,
)

# Tolerancia dos limites do recorte, em pixels da fixture. A borda esperada e
# "a beirada de dentro da linha + a margem", e a margem e fracionaria.
FOLGA = 12


# ---------------------------------------------------------------------------
# Calibracao
# ---------------------------------------------------------------------------


def test_parametros_de_calibracao_preservados():
    """Os valores medidos no acervo nao podem ter mudado em settings.py."""
    assert settings.RECORTE_FATOR_DETECCAO == 0.125
    assert settings.RECORTE_MARGEM_FRAC == 0.004
    assert settings.RECORTE_EIXOS == settings.RECORTE_EIXO_AMBOS
    assert settings.RECORTE_PERFIL_PADRAO == "auto"
    assert settings.RECORTE_ESPESSURA_MAX_FRAC == 0.04
    assert settings.RECORTE_INCLINACAO_MAX_GRAUS == 3.0
    assert settings.RECORTE_PONTE_FRAC == 0.04
    assert settings.RECORTE_DISTANCIA_MIN_FRAC == 0.02
    assert settings.RECORTE_FORCA_RELATIVA == 0.55
    assert settings.RECORTE_FORCA_MINIMA == 0.35
    assert settings.RECORTE_FORCA_ANCORA == 0.55
    assert settings.RECORTE_CORTAR_MOLDURA is True
    assert settings.RECORTE_NIVEIS == (
        (0.55, 0.50), (0.40, 0.40), (0.28, 0.30), (0.18, 0.22),
    )


def test_niveis_vao_do_mais_duro_ao_mais_frouxo():
    """
    A ordem dos niveis E a calibracao: o detector para no primeiro que da
    par valido, entao uma foto limpa nunca pode pagar o preco (em falso
    positivo) de uma foto ocluida.
    """
    alturas = [altura for altura, _ in settings.RECORTE_NIVEIS]
    limiares = [limiar for _, limiar in settings.RECORTE_NIVEIS]
    assert alturas == sorted(alturas, reverse=True)
    assert limiares == sorted(limiares, reverse=True)


def test_forca_minima_abaixo_do_pior_par_do_acervo():
    """
    O piso absoluto de forca precisa ter folga: no acervo real o par
    escolhido nunca ficou abaixo de 0.48.
    """
    assert settings.RECORTE_FORCA_MINIMA < 0.48
    # ...e ainda assim acima do que uma foto fora de foco produz (~0.29).
    assert settings.RECORTE_FORCA_MINIMA > 0.29


def test_perfis_de_armadilha_cobrem_as_larguras_medidas():
    """
    Faixas medidas no acervo: azul 0.464-0.482 e amarela 0.525-0.560 da
    largura da foto. Cada perfil precisa conter a sua, e 'auto' as duas.
    """
    auto = settings.RECORTE_PERFIS["auto"]
    azul = settings.RECORTE_PERFIS["azul"]
    amarela = settings.RECORTE_PERFIS["amarela"]
    for perfil, (menor, maior) in ((azul, (0.464, 0.482)),
                                   (amarela, (0.525, 0.560)),
                                   (auto, (0.464, 0.560))):
        assert perfil["largura_min_frac"] < menor
        assert perfil["largura_max_frac"] > maior
    # Os perfis nomeados precisam ser MAIS apertados que o auto, senao nao
    # servem para nada.
    for perfil in (azul, amarela):
        assert perfil["largura_min_frac"] >= auto["largura_min_frac"]
        assert perfil["largura_max_frac"] <= auto["largura_max_frac"]


def test_defaults_das_funcoes_seguem_settings():
    """
    Nenhum default duplicado no codigo: as funcoes publicas resolvem tudo em
    settings.py, entao o default na assinatura e sempre None.
    """
    for funcao in (detectar_crop_box, recortar_em_resolucao_cheia):
        defaults = {
            nome: p.default
            for nome, p in inspect.signature(funcao).parameters.items()
            if p.default is not inspect.Parameter.empty
        }
        assert set(defaults.values()) == {None}, funcao.__name__


def test_perfil_invalido_e_recusado():
    with pytest.raises(ValueError, match="Perfil de armadilha invalido"):
        resolver_perfil("verde")


# ---------------------------------------------------------------------------
# Deteccao - a cor nao entra na conta
# ---------------------------------------------------------------------------


def test_amarela_e_azul_dao_o_mesmo_recorte(foto_valida, foto_azul):
    """
    A regressao que originou este detector: a armadilha AZUL saia recortada
    errado porque o algoritmo antigo binarizava o cinza, e azul e escuro em
    cinza. Mesma geometria tem que dar o mesmo crop box, em qualquer cor.
    """
    _, info_amarela = recortar_em_resolucao_cheia(str(foto_valida),
                                                  fator_deteccao=1.0)
    _, info_azul = recortar_em_resolucao_cheia(str(foto_azul),
                                               fator_deteccao=1.0)
    assert info_amarela["sucesso"] is True
    assert info_azul["sucesso"] is True
    assert info_amarela["crop_box_px"] == info_azul["crop_box_px"]


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_crop_fica_dentro_do_quadrante_central(nome_fixture, request):
    """
    O recorte tem que cair ENTRE as linhas da celula central nos dois eixos,
    e nao entre as linhas dos quadrantes vizinhos.
    """
    caminho = request.getfixturevalue(nome_fixture)
    _, info = recortar_em_resolucao_cheia(str(caminho), fator_deteccao=1.0)
    y1, y2, x1, x2 = info["crop_box_cheia_px"]
    esquerda, direita = CROP_ESPERADO_X
    topo, base = CROP_ESPERADO_Y

    assert esquerda < x1 < esquerda + FOLGA, "borda esquerda fora do quadrante"
    assert direita - FOLGA < x2 < direita, "borda direita fora do quadrante"
    assert topo < y1 < topo + FOLGA, "borda de cima fora do quadrante"
    assert base - FOLGA < y2 < base, "borda de baixo fora do quadrante"


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_a_linha_da_grade_nao_entra_no_quadrante(nome_fixture, request):
    """
    Recortar no CENTRO da linha deixaria meia linha preta na entrega. O
    corte sai da beirada de DENTRO da linha, mais a margem.
    """
    caminho = request.getfixturevalue(nome_fixture)
    recortada, _ = recortar_em_resolucao_cheia(str(caminho), fator_deteccao=1.0)
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60

    # Nenhuma linha/coluna da borda pode ser majoritariamente escura.
    assert escuros[0].mean() < 0.5 and escuros[-1].mean() < 0.5
    assert escuros[:, 0].mean() < 0.5 and escuros[:, -1].mean() < 0.5


def test_recorta_nos_quatro_lados(foto_valida):
    """
    A celula da grade e menor que a foto: entregar a altura inteira levaria
    junto a linha horizontal e tiras dos quadrantes de cima e de baixo.
    """
    recortada, info = recortar_em_resolucao_cheia(str(foto_valida),
                                                  fator_deteccao=1.0)
    y1, y2, x1, x2 = info["crop_box_cheia_px"]
    assert (y1, y2) != (0, ALTURA_VALIDA)
    assert (x1, x2) != (0, LARGURA_VALIDA)
    assert recortada.shape[0] < ALTURA_VALIDA
    assert recortada.shape[1] < LARGURA_VALIDA


def test_so_vertical_devolve_a_altura_inteira(foto_valida):
    """O modo historico continua disponivel para quem depende dele."""
    imagem = cv2.imread(str(foto_valida))
    info = detectar_crop_box(imagem, eixos=settings.RECORTE_EIXO_SO_VERTICAL,
                             cortar_moldura=False)
    y1, y2, x1, x2 = info["crop_box_px"]
    assert info["sucesso"] is True
    assert (y1, y2) == (0, ALTURA_VALIDA)
    # ...e as laterais continuam sendo recortadas normalmente.
    assert (x1, x2) != (0, LARGURA_VALIDA)


def test_so_vertical_ainda_apara_a_moldura(foto_valida):
    """
    Sem o corte no eixo Y, o aparo da moldura e a unica defesa contra a
    faixa preta do topo. Ele nao pode desligar junto.
    """
    imagem = cv2.imread(str(foto_valida))
    info = detectar_crop_box(imagem, eixos=settings.RECORTE_EIXO_SO_VERTICAL)
    y1, y2, _, _ = info["crop_box_px"]
    assert y1 > 0
    assert y2 == ALTURA_VALIDA  # o aparo nao vira recorte de quadrante


def test_inclinacao_da_grade_e_corrigida(foto_inclinada):
    """
    1.5 grau de giro espalha a linha por dezenas de colunas. Sem a correcao
    a projecao nao forma pico e o recorte vira a foto inteira.
    """
    recortada, info = recortar_em_resolucao_cheia(str(foto_inclinada),
                                                  fator_deteccao=1.0)
    assert info["sucesso"] is True
    assert info["inclinacao_graus"] == pytest.approx(-INCLINACAO_GRAUS, abs=0.4)
    # E a linha girada continua fora do quadrante, inclusive nos cantos.
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60
    assert escuros[0].mean() < 0.5 and escuros[-1].mean() < 0.5
    assert escuros[:, 0].mean() < 0.5 and escuros[:, -1].mean() < 0.5


def test_a_moldura_da_foto_nao_entra_no_quadrante(foto_valida):
    """A faixa preta do topo (fora da armadilha) fica de fora."""
    from tests.fixtures.gerar_fixtures import ALTURA_MOLDURA

    _, info = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=1.0)
    y1, _, _, _ = info["crop_box_cheia_px"]
    assert y1 >= ALTURA_MOLDURA


def test_a_linha_do_vizinho_nao_vira_borda(foto_valida):
    """
    A fixture tem uma linha em x=900 que so cobre 35% da altura. Ela nao
    pode ser escolhida como borda do quadrante central.
    """
    imagem = cv2.imread(str(foto_valida))
    info = detectar_crop_box(imagem)
    _, _, _, x2 = info["crop_box_px"]
    assert x2 < 900 - ESPESSURA_LINHA


# ---------------------------------------------------------------------------
# Resolucao cheia e fidelidade dos pixels
# ---------------------------------------------------------------------------


def test_deteccao_usa_copia_reduzida_e_crop_sai_em_resolucao_cheia(foto_valida):
    """A deteccao roda reduzida; o crop e aplicado na imagem original."""
    recortada, info = recortar_em_resolucao_cheia(str(foto_valida),
                                                  fator_deteccao=0.75)
    altura_det, largura_det = info["dim_deteccao"]
    assert (largura_det, altura_det) == (
        int(LARGURA_VALIDA * 0.75), int(ALTURA_VALIDA * 0.75),
    )
    assert largura_det < LARGURA_VALIDA
    assert info["dim_original"] == (ALTURA_VALIDA, LARGURA_VALIDA)

    # O quadrante sai na escala CHEIA: a caixa em resolucao cheia e maior
    # que a mesma caixa medida na copia de deteccao, exatamente 1/fator.
    dy1, dy2, dx1, dx2 = info["crop_box_px"]
    cy1, cy2, cx1, cx2 = info["crop_box_cheia_px"]
    assert recortada.shape[:2] == (cy2 - cy1, cx2 - cx1)
    assert (cx2 - cx1) == pytest.approx((dx2 - dx1) / 0.75, abs=2)
    assert (cy2 - cy1) == pytest.approx((dy2 - dy1) / 0.75, abs=2)


def test_fator_afrouxa_para_nao_sumir_com_a_linha_em_foto_pequena(foto_valida):
    """
    O fator e calibrado para foto de 9600 px. Aplicado cru numa foto de
    1200 px, a linha da grade ficaria com pouco mais de um pixel e a
    deteccao falharia sem motivo - por isso existe o piso de largura.
    """
    _, info = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=0.01)
    _, largura_det = info["dim_deteccao"]
    assert largura_det >= settings.RECORTE_LARGURA_MINIMA_DETECCAO
    assert info["sucesso"] is True


def test_piso_de_largura_nunca_amplia_a_foto(foto_sem_grade):
    """Foto menor que o piso e usada como esta - ampliar nao inventa linha."""
    imagem = cv2.imread(str(foto_sem_grade))
    _, info = recortar_em_resolucao_cheia(str(foto_sem_grade))
    assert info["dim_deteccao"] == imagem.shape[:2]


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
    assert info_a["picos_h"] == info_b["picos_h"]


def test_fator_de_deteccao_nao_muda_o_enquadramento(foto_valida):
    """
    Todos os parametros sao fracionarios: mudar o fator muda o custo, nao o
    resultado. A tolerancia e a resolucao da propria copia reduzida.
    """
    _, cheio = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=1.0)
    _, reduzido = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=0.5)
    for a, b in zip(cheio["crop_box_cheia_px"], reduzido["crop_box_cheia_px"]):
        assert abs(a - b) <= FOLGA


def test_falha_de_deteccao_devolve_imagem_cheia(foto_sem_grade):
    """Comportamento validado no Colab: sem linhas -> imagem inteira."""
    imagem_original = cv2.imread(str(foto_sem_grade))
    devolvida, info = recortar_em_resolucao_cheia(str(foto_sem_grade))

    assert info["sucesso"] is False
    assert np.array_equal(devolvida, imagem_original)


def test_inclinacao_nunca_passa_do_limite_configurado(foto_sem_grade):
    """
    Mesmo sem grade nenhuma, a busca fina nao pode escapar da faixa: um
    angulo fora dela abriria uma margem grande demais no recorte.
    """
    imagem = cv2.imread(str(foto_sem_grade))
    info = detectar_crop_box(imagem)
    assert abs(info["inclinacao_graus"]) <= settings.RECORTE_INCLINACAO_MAX_GRAUS


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def test_processar_foto_grava_o_quadrante(foto_valida, tmp_path):
    resultado = processar_foto(foto_valida, pasta_saida=tmp_path,
                               mover_falhas=False)
    assert resultado["sucesso"] is True
    assert resultado["deteccao_ok"] is True
    assert (tmp_path / "foto_grade_valida.png").exists()


def test_processar_foto_aceita_perfil_nomeado(foto_azul, tmp_path):
    """--perfil azul so aperta a faixa; o quadrante sai igual ao do auto."""
    auto = processar_foto(foto_azul, pasta_saida=tmp_path / "auto",
                          mover_falhas=False)
    azul = processar_foto(foto_azul, pasta_saida=tmp_path / "azul",
                          perfil="azul", mover_falhas=False)
    assert auto["info"]["crop_box_px"] == azul["info"]["crop_box_px"]
    assert azul["info"]["perfil"] == "azul"


def test_processar_item_aceita_par_caminho_e_nome(foto_valida, tmp_path):
    resultado = processar_item((str(foto_valida), "VARD7"), pasta_saida=tmp_path,
                               mover_falhas=False)
    assert resultado["sucesso"] is True
    assert (tmp_path / "VARD7.png").exists()


def test_info_do_recorte_e_serializavel(foto_valida, tmp_path):
    """O JSON de diagnostico grava o info inteiro - nada de numpy nele."""
    resultado = processar_foto(foto_valida, pasta_saida=tmp_path,
                               mover_falhas=False)
    json.dumps(resultado["info"])
