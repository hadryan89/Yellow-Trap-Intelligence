"""
Regressao do recorte contra o ACERVO REAL de fotos.

As fixtures sinteticas provam que a logica esta certa; so o acervo prova que
a CALIBRACAO esta certa. Estes testes rodam sobre as fotos de verdade e sao
pulados quando as pastas nao estao no disco (maquina de CI, clone limpo).

    pytest tests/test_recorte_acervo.py -m acervo

O criterio nao e "achou alguma coisa", e o que importa para o proximo
estagio do sistema:

  1. cobertura   - toda foto de armadilha tem que virar quadrante;
  2. consistencia- o mesmo quadrante fotografado varias vezes tem que sair
                   sempre do mesmo tamanho (e a metrica que denunciou o bug
                   do azul: a largura variava de 0.47 a 0.95 da foto);
  3. os TRACOS   - a linha da grade tem que sair NA BORDA do quadrante (e
                   com ela que a placa e remontada depois) e nao no miolo;
  4. precisao    - e o corte tem que cair na BEIRADA do traco: nem antes
                   (sobra papel), nem no meio dele (falta traco);
  5. limpeza     - o preto encostado na borda tem que ser o traco e mais
                   nada: a moldura da foto nao pode entrar junto.

Cada foto e lida UMA vez: a deteccao e as tres medidas de qualidade saem da
mesma passagem (o acervo tem 105 fotos de 9600x5400).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from config import settings
from src import recorte as R

pytestmark = [pytest.mark.acervo, pytest.mark.lento]

# (nome, pasta, perfil, largura mediana esperada, desvio maximo tolerado)
# As medianas sao do recorte COM traco (RECORTE_BORDA = 'linha'), que vai ate
# a beirada externa da linha. Sem traco elas caem para 0.457 (azul) e 0.535
# (amarela) - a diferenca e a espessura do traco, dos dois lados.
ACERVOS = [
    ("azul", settings.PASTA_DADOS / "azuis" / "BlueTrap", "azul", 0.495, 0.02),
    ("amarela", settings.PASTA_ENTRADA, "amarela", 0.553, 0.02),
]

# Fotos que NAO sao de armadilha (fora de foco, sem grade). Elas tem que ser
# recusadas, entao nao entram na conta de cobertura.
NAO_SAO_ARMADILHA = {"20241126_205023.JPG", "20241126_205222.JPG"}

# Faixa de cada borda onde o traco da grade e ESPERADO, em fracao do lado.
# O corte para na propria linha, entao o traco fica nos primeiros ~2% do
# lado; 10% cobre isso com folga (grade torta desloca o traco ao longo da
# borda) e ainda deixa o miolo inteiro sob vigilancia - uma linha que
# "sobrou" por deteccao errada cai no meio do quadrante, nao a 10% da borda.
BORDA_FRAC = 0.10

# Ate onde o PRETO pode ir, a partir de cada borda, em fracao do lado. O
# traco mede ~2% do lado; 3% deixa margem para o traco mais grosso do acervo
# e ainda barra faixa de moldura, que avanca 5% ou mais para dentro.
PRETO_MAX_FRAC = 0.03

# Fracao minima dos lados que tem que trazer o traco da grade, por acervo.
# Nao e a mesma nas duas porque nao depende so do recorte: na AMARELA o
# quadrante ocupa 0,97 da altura da foto, entao na maioria das fotos as
# linhas horizontais ficaram FORA do enquadramento da camera - nao ha traco
# para entregar em cima e embaixo. Nesses lados o recorte para onde o papel
# acaba (ver test_o_corte_cai_na_beirada_do_traco).
LADOS_COM_TRACO_MIN = {"azul": 0.90, "amarela": 0.75}

# Onde o corte caiu em relacao ao traco, de 0 a 1 (ver _onde_caiu_o_corte):
# 0 = em cima do ponto mais escuro (cortou o traco ao meio), 1 = ja no papel
# (sobrou margem). O alvo e a beirada externa do traco - perto de 1, sem
# chegar la. Medido no acervo: mediana 0,65 a 0,88 por lado.
CORTE_MEDIANO_MIN = 0.50
# E no maximo esta fracao dos lados pode cair na metade escura do traco.
CORTANDO_MAX = 0.40


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


def _linhas_no_miolo(imagem, borda_frac=BORDA_FRAC, forca_min=0.55):
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


def _lados_com_traco(quadrante):
    """
    Quantos dos 4 lados do quadrante trazem o traco da grade.

    O criterio e RELATIVO (percentil de brilho do proprio quadrante) porque
    o papel azul ja e escuro em absoluto - so o traco e escuro *para aquela
    foto*. Vale traco a ate BORDA_FRAC da borda: o corte deixa de proposito
    uma folga de papel entre o traco e a beirada da imagem.
    """
    valor = cv2.cvtColor(quadrante, cv2.COLOR_BGR2HSV)[..., 2]
    escuro = valor < np.percentile(valor, 20)
    banda = max(4, int(BORDA_FRAC * quadrante.shape[1]))
    lados = (escuro[:banda].mean(1), escuro[-banda:].mean(1),
             escuro[:, :banda].mean(0), escuro[:, -banda:].mean(0))
    return sum(1 for lado in lados if lado.max() > 0.7)


def _onde_caiu_o_corte(quadrante):
    """
    Por lado, onde a beirada do recorte caiu em relacao ao traco.

    Le o perfil de brilho a partir de cada borda e devolve
    t = (brilho_da_beirada - mais_escuro) / (papel - mais_escuro):

      t ~ 0  o corte passou pelo miolo do traco (entregou meia linha)
      t ~ 1  o corte parou no papel (sobrou margem)

    O alvo e a beirada externa do traco, perto de 1 sem chegar la.

    Duas situacoes ficam de fora da conta, porque nelas nao ha traco para
    medir: borda sem contraste nenhum (linha ocluida por uma folha, ou fora
    do enquadramento) e borda de PAPEL - a que o recorte entrega quando a
    celula sai pela beirada da foto e o corte para onde o papel acaba. Nessa
    ultima o ponto mais escuro cai na propria beirada, entao ela e
    reconhecida por ai.
    """
    valor = cv2.cvtColor(quadrante, cv2.COLOR_BGR2HSV)[..., 2].astype(float)
    papel = float(np.median(valor))
    faixa = max(4, int(BORDA_FRAC * quadrante.shape[1]))
    perfis = (valor[:faixa].mean(1), valor[-faixa:][::-1].mean(1),
              valor[:, :faixa].mean(0), valor[:, ::-1][:, :faixa].mean(0))
    saida = []
    for perfil in perfis:
        escuro = float(perfil.min())
        if papel - escuro < 8:            # esta borda nao tem traco
            continue
        if int(np.argmin(perfil)) <= 2:   # borda de papel, nao traco
            continue
        saida.append((perfil[0] - escuro) / (papel - escuro))
    return saida


def _profundidade_preta(quadrante):
    """
    Quao fundo entra o preto a partir da borda, em fracao do lado (o pior
    dos 4 lados).

    Nao adianta mais perguntar "a beirada e escura?": com o corte parando
    na linha, ela e escura de proposito. O que denuncia moldura e o preto
    que NAO acaba - o traco tem a espessura dele e termina em papel.
    """
    valor = cv2.cvtColor(quadrante, cv2.COLOR_BGR2HSV)[..., 2]
    preto = valor < settings.RECORTE_MOLDURA_BRILHO_MAX

    def fundo(fileiras):
        densas = fileiras >= settings.RECORTE_MOLDURA_FRACAO_MIN
        return int(len(densas) if densas.all() else np.argmin(densas))

    altura, largura = preto.shape
    return max(fundo(preto.mean(1)) / altura,
               fundo(preto[::-1].mean(1)) / altura,
               fundo(preto.mean(0)) / largura,
               fundo(preto[:, ::-1].mean(0)) / largura)


@pytest.fixture(scope="module")
def resultados():
    """
    Roda a deteccao UMA vez por foto e guarda as medidas de qualidade.

    Cada item: (arquivo, info, medidas). `medidas` e None quando nao houve
    deteccao - nesse caso a entrega e a foto inteira e nao ha quadrante para
    medir.
    """
    saida = {}
    for nome, pasta, _, _, _ in ACERVOS:
        fotos = _fotos(pasta)
        if not fotos:
            continue
        itens = []
        for caminho in fotos:
            reduzida = _reduzida(caminho)
            info = R.detectar_crop_box(reduzida)
            medidas = None
            if info["sucesso"]:
                y1, y2, x1, x2 = info["crop_box_px"]
                quadrante = reduzida[y1:y2, x1:x2]
                medidas = {
                    "miolo": _linhas_no_miolo(quadrante),
                    "lados": _lados_com_traco(quadrante),
                    "corte": _onde_caiu_o_corte(quadrante),
                    "preto": _profundidade_preta(quadrante),
                }
                del quadrante
            itens.append((caminho.name, info, medidas))
            del reduzida
        saida[nome] = itens
    if not saida:
        pytest.skip("acervo de fotos reais nao esta neste disco")
    return saida


def _acervo(nome, resultados):
    if nome not in resultados:
        pytest.skip(f"acervo '{nome}' nao esta neste disco")
    return resultados[nome]


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_toda_foto_de_armadilha_vira_quadrante(nome, pasta, perfil, largura,
                                               desvio, resultados):
    falhas = [arquivo for arquivo, info, _ in _acervo(nome, resultados)
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
    larguras = [(x2 - x1) / info["dim_deteccao"][1]
                for _, info, _ in _acervo(nome, resultados) if info["sucesso"]
                for _, _, x1, x2 in [info["crop_box_px"]]]
    assert np.median(larguras) == pytest.approx(largura, abs=desvio)
    assert np.std(larguras) < 0.02, f"larguras dispersas: {sorted(larguras)[:5]}"


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_o_traco_da_grade_sai_nas_bordas(nome, pasta, perfil, largura, desvio,
                                         resultados):
    """
    O que a etapa de montagem consome: o quadrante entregue tem que trazer o
    traco da grade rente a borda, nos quatro lados.

    A exigencia e agregada, e o piso muda com o acervo (ver
    LADOS_COM_TRACO_MIN): uma linha pode estar apagada no papel, coberta por
    praga ou - na amarela - fora do enquadramento da camera, e nada disso e
    defeito do recorte. No acervo atual a medida fica em 93% (azul) e 82%
    (amarela).
    """
    medidas = [m for _, _, m in _acervo(nome, resultados) if m]
    lados = sum(m["lados"] for m in medidas)
    minimo = LADOS_COM_TRACO_MIN[nome]
    assert lados / (4 * len(medidas)) >= minimo, (
        f"tracos faltando nas bordas: {lados}/{4 * len(medidas)}")


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_o_corte_cai_na_beirada_do_traco(nome, pasta, perfil, largura, desvio,
                                         resultados):
    """
    Precisao do corte: ele tem que parar na beirada EXTERNA do traco.

    A regressao que este teste trava: medir a espessura da linha pela
    mascara do blackhat, que marca so o nucleo escuro. A tinta desbota nas
    beiradas e nao responde ao realce, entao o corte caia no meio do traco
    e entregava meia linha - 51% a 77% dos lados, nos dois acervos.
    """
    medidos = [t for _, _, m in _acervo(nome, resultados) if m
               for t in m["corte"]]
    assert medidos, "nenhum lado com traco para medir"
    mediana = float(np.median(medidos))
    cortando = float(np.mean([t < 0.35 for t in medidos]))
    assert mediana >= CORTE_MEDIANO_MIN, (
        f"corte caindo dentro do traco: mediana {mediana:.2f}")
    assert cortando <= CORTANDO_MAX, (
        f"{100*cortando:.0f}% dos lados cortam o traco ao meio")


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_o_preto_da_borda_acaba_no_traco(nome, pasta, perfil, largura,
                                         desvio, resultados):
    """
    A contrapartida do teste acima: o preto encostado na borda tem que ser
    o traco da grade e mais nada.

    Sem esta trava, encostar o corte na linha traria junto a faixa preta
    das fotos em que a celula sai pela beirada do enquadramento - ali o
    detector ancora na borda escura da propria foto, e o corte devolveria
    a moldura como se fosse traco. No acervo o preto para em ate 24 px de
    4800 (0,5% do lado).
    """
    sujos = [(arquivo, round(m["preto"], 3))
             for arquivo, _, m in _acervo(nome, resultados)
             if m and m["preto"] > PRETO_MAX_FRAC]
    assert not sujos, f"preto entrando alem do traco: {sujos}"


@pytest.mark.parametrize("nome,pasta,perfil,largura,desvio", ACERVOS,
                         ids=[a[0] for a in ACERVOS])
def test_nenhuma_linha_da_grade_sobra_no_miolo(nome, pasta, perfil, largura,
                                               desvio, resultados):
    """
    O criterio final de qualidade: linha de grade so pode estar na borda do
    quadrante. Uma no MIOLO significa que o recorte pulou uma linha e
    entregou dois quadrantes colados - antes desta calibracao, so 7 das 65
    fotos azuis passavam aqui.
    """
    sujos = [(arquivo, m["miolo"]) for arquivo, _, m in _acervo(nome, resultados)
             if m and m["miolo"]]
    assert not sujos, f"linha de grade no miolo do quadrante: {sujos}"
