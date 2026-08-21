"""
Testes do protocolo 2 (recorte).

O objetivo NAO e testar "se o codigo roda", e sim travar o comportamento que
foi medido no acervo real:

  * os parametros de calibracao nao podem mudar sem alguem perceber;
  * o detector e CEGO A COR: armadilha amarela e azul, mesma geometria,
    tem que sair com o mesmo crop box;
  * a linha da grade sai nas quatro bordas do quadrante, E NADA DEPOIS
    DELA - e dela que a etapa seguinte precisa para remontar a placa - e o
    modo 'dentro' continua entregando o quadrante sem traco nenhum;
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
    resolver_borda,
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

# Tolerancia dos limites do recorte, em pixels da fixture. A margem e
# fracionaria, entao a borda nunca cai num pixel exato.
FOLGA = 12
# Com o default 'linha' a borda cai na beirada EXTERNA do traco: o corte anda
# exatamente a espessura da linha para fora, sem folga nenhuma depois dela.
FOLGA_TRACO = ESPESSURA_LINHA + FOLGA


# ---------------------------------------------------------------------------
# Calibracao
# ---------------------------------------------------------------------------


def test_parametros_de_calibracao_preservados():
    """Os valores medidos no acervo nao podem ter mudado em settings.py."""
    assert settings.RECORTE_FATOR_DETECCAO == 0.125
    assert settings.RECORTE_MARGEM_FRAC == 0.004
    assert settings.RECORTE_BORDA == settings.RECORTE_BORDA_LINHA
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


def test_borda_invalida_e_recusada():
    with pytest.raises(ValueError, match="Borda invalida"):
        resolver_borda("mais_ou_menos_no_meio")


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
    O recorte tem que ABRACAR as linhas da celula central nos dois eixos, e
    nao alcancar as linhas dos quadrantes vizinhos.

    Com o default 'linha' cada borda cai um pouco FORA do traco (a espessura
    dele mais a folga). A linha do vizinho esta a uma celula inteira de
    distancia, entao a checagem continua valendo como enquadramento.
    """
    caminho = request.getfixturevalue(nome_fixture)
    _, info = recortar_em_resolucao_cheia(str(caminho), fator_deteccao=1.0)
    y1, y2, x1, x2 = info["crop_box_cheia_px"]
    esquerda, direita = CROP_ESPERADO_X
    topo, base = CROP_ESPERADO_Y

    assert esquerda - FOLGA_TRACO < x1 < esquerda, "borda esquerda fora do quadrante"
    assert direita < x2 < direita + FOLGA_TRACO, "borda direita fora do quadrante"
    assert topo - FOLGA_TRACO < y1 < topo, "borda de cima fora do quadrante"
    assert base < y2 < base + FOLGA_TRACO, "borda de baixo fora do quadrante"


def _lados_com_traco(recortada, banda_frac=0.06):
    """
    Quantos dos 4 lados tem um traco escuro contiguo perto da borda.

    PERTO da borda, nao EM cima dela: o corte deixa de proposito a folga da
    inclinacao entre o traco e a beirada da imagem, entao a primeira coluna
    nem sempre e a linha.
    """
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60
    banda = max(4, int(banda_frac * recortada.shape[1]))
    lados = (escuros[:banda].mean(1), escuros[-banda:].mean(1),
             escuros[:, :banda].mean(0), escuros[:, -banda:].mean(0))
    return sum(1 for lado in lados if lado.max() > 0.9)


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_a_linha_da_grade_sai_no_quadrante(nome_fixture, request):
    """
    O default entrega os QUATRO tracos da grade: sao eles que permitem
    juntar os 40 quadrantes de volta na placa com a grade visivel.
    """
    caminho = request.getfixturevalue(nome_fixture)
    recortada, info = recortar_em_resolucao_cheia(str(caminho),
                                                  fator_deteccao=1.0)
    assert info["borda"] == settings.RECORTE_BORDA_LINHA
    assert _lados_com_traco(recortada) == 4


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_nao_sobra_margem_depois_do_traco(nome_fixture, request):
    """
    O quadrado tem que ser a propria beirada do arquivo.

    Nada de papel, de quadrante vizinho ou de moldura depois do traco: a
    PRIMEIRA fileira de cada lado ja e o traco. E o que a montagem da placa
    pede - quadrado encostando em quadrado, sem faixa entre eles.
    """
    caminho = request.getfixturevalue(nome_fixture)
    recortada, info = recortar_em_resolucao_cheia(str(caminho),
                                                  fator_deteccao=1.0)
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60
    # O traco comeca na fileira 0 - a tolerancia de RENTE e so o
    # arredondamento da caixa, que e medida em fracao da foto.
    RENTE = 3
    assert escuros[:RENTE].mean(1).max() > 0.9, "sobrou margem antes do traco de cima"
    assert escuros[-RENTE:].mean(1).max() > 0.9, "sobrou margem depois do traco de baixo"
    assert escuros[:, :RENTE].mean(0).max() > 0.9, "sobrou margem antes do traco esquerdo"
    assert escuros[:, -RENTE:].mean(0).max() > 0.9, "sobrou margem depois do traco direito"

    # E o corte cai na beirada EXTERNA do traco, nao alem dela: a fixture
    # tem a linha em CROP_ESPERADO_X com ESPESSURA_LINHA de espessura.
    _, _, x1, x2 = info["crop_box_cheia_px"]
    esquerda, direita = CROP_ESPERADO_X
    assert x1 == pytest.approx(esquerda - ESPESSURA_LINHA // 2, abs=3)
    assert x2 == pytest.approx(direita + ESPESSURA_LINHA // 2, abs=3)


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_borda_dentro_entrega_o_quadrante_sem_traco(nome_fixture, request):
    """
    O comportamento anterior continua a uma flag de distancia: corte pela
    beirada de DENTRO da linha, nada de traco na entrega.
    """
    caminho = request.getfixturevalue(nome_fixture)
    recortada, _ = recortar_em_resolucao_cheia(
        str(caminho), fator_deteccao=1.0, borda=settings.RECORTE_BORDA_DENTRO)
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60

    # Nenhuma linha/coluna da borda pode ser majoritariamente escura.
    assert escuros[0].mean() < 0.5 and escuros[-1].mean() < 0.5
    assert escuros[:, 0].mean() < 0.5 and escuros[:, -1].mean() < 0.5


@pytest.mark.parametrize("nome_fixture", ["foto_valida", "foto_azul"])
def test_meia_linha_corta_no_centro_do_traco(nome_fixture, request):
    """
    'meia_linha' existe para a placa remontada nao ficar com o traco dobrado
    na emenda: cada quadrante leva metade dele. O corte fica entre o de
    'dentro' e o de 'linha' - e em cima do centro do traco impresso.
    """
    caminho = request.getfixturevalue(nome_fixture)
    caixas = {}
    for borda in settings.RECORTE_BORDAS_VALIDAS:
        _, info = recortar_em_resolucao_cheia(str(caminho), fator_deteccao=1.0,
                                              borda=borda)
        caixas[borda] = info["crop_box_cheia_px"]

    _, _, x1_fora, x2_fora = caixas[settings.RECORTE_BORDA_LINHA]
    _, _, x1_meio, x2_meio = caixas[settings.RECORTE_BORDA_MEIA_LINHA]
    _, _, x1_dentro, x2_dentro = caixas[settings.RECORTE_BORDA_DENTRO]
    assert x1_fora < x1_meio < x1_dentro
    assert x2_dentro < x2_meio < x2_fora

    esquerda, direita = CROP_ESPERADO_X
    assert x1_meio == pytest.approx(esquerda, abs=2)
    assert x2_meio == pytest.approx(direita, abs=2)


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
    # E o traco girado cabe INTEIRO no quadrante: a folga do corte cresce
    # com a inclinacao justamente porque a linha deriva nas pontas.
    escuros = cv2.cvtColor(recortada, cv2.COLOR_BGR2GRAY) < 60
    # Num traco girado nenhuma COLUNA e escura de ponta a ponta (ele anda de
    # uma para a outra), entao a pergunta certa e por LINHA: a linha da
    # imagem tem que encontrar o traco dentro da faixa da borda.
    #
    # 0.90, e nao 1.0, porque o corte nao deixa margem: numa das pontas o
    # traco girado sai do quadro (ver `_bordas`). Sobra margem ou sobra
    # traco - num recorte retangular nao da para ter os dois, e a escolha
    # aqui e nao sobrar margem.
    banda = int(0.10 * recortada.shape[1])
    assert escuros[:, :banda].any(1).mean() > 0.90, "traco esquerdo cortado"
    assert escuros[:, -banda:].any(1).mean() > 0.90, "traco direito cortado"


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
    resultado.

    A tolerancia e maior que FOLGA porque a beirada do traco e medida no
    BRILHO, e o brilho da beirada depende do borrao da copia reduzida - ou
    seja, da escala. No acervo real (fator 0.125 contra 0.25) a caixa anda
    no maximo 28 px em ~5.000, meio por cento; na fixture o desvio e maior
    porque a moldura preta encosta na linha de cima e o agrupamento junta
    as duas, que e o caso mais dificil que ela cobre de proposito.
    """
    FOLGA_ESCALA = 20
    _, cheio = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=1.0)
    _, reduzido = recortar_em_resolucao_cheia(str(foto_valida), fator_deteccao=0.5)
    for a, b in zip(cheio["crop_box_cheia_px"], reduzido["crop_box_cheia_px"]):
        assert abs(a - b) <= FOLGA_ESCALA


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
