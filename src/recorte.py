"""
Protocolo 2 - Recorte do quadrante central.

Cada foto individual captura o quadrante alvo mais pedacos dos vizinhos
(limitacao do microscopio). O algoritmo detecta as linhas pretas da grade
YellowTrap e recorta apenas o quadrante central.

As fotos sao landscape mostrando 3 quadrantes horizontalmente - por isso
modo='so_vertical' (apenas linhas verticais sao detectadas).

QUALIDADE
---------
A deteccao roda sobre uma copia REDUZIDA em memoria (fator 0.25) por
performance, mas o crop e aplicado na imagem ORIGINAL em resolucao cheia
atraves de coordenadas fracionais. Nenhum pixel entregue passa por
redimensionamento ou re-encoding lossy.

Logica migrada VERBATIM do Colab. Os parametros default sao os validados:
fator_deteccao=0.25, margem=8, modo='so_vertical', span_v_inicial_frac=0.5,
span_h_inicial_frac=0.5, span_minimo_frac=0.15, dilatar=True.
"""

from __future__ import annotations

import gc
import time
from pathlib import Path

import cv2
import numpy as np

from config import settings
from src.exportacao import extensao_do_formato, salvar_recortada
from src.utils import imread_fallback, obter_logger, registrar_falha

logger = obter_logger(__name__)

__all__ = [
    "detectar_crop_box",
    "recortar_em_resolucao_cheia",
    "salvar_recortada",
    "processar_foto",
]


# ---------------------------------------------------------------------------
# Funcoes validadas no Colab - NAO ALTERAR A LOGICA NEM OS PARAMETROS
# ---------------------------------------------------------------------------


def _detectar_linhas_em_eixo(binario, eixo, span_inicial, span_minimo,
                             limiar_frac=0.25, min_dist=30):
    """Detecta linhas adaptivamente reduzindo o kernel ate achar >=2 picos."""
    altura, largura = binario.shape
    span = span_inicial
    ultima_proj = np.zeros(largura if eixo == 'vertical' else altura)
    ultima_img = np.zeros_like(binario)

    while span >= span_minimo:
        if eixo == 'vertical':
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, span))
            linhas = cv2.morphologyEx(binario, cv2.MORPH_OPEN, kernel, iterations=1)
            projecao = np.sum(linhas > 0, axis=0)
        else:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (span, 1))
            linhas = cv2.morphologyEx(binario, cv2.MORPH_OPEN, kernel, iterations=1)
            projecao = np.sum(linhas > 0, axis=1)

        ultima_proj = projecao
        ultima_img = linhas

        if projecao.max() == 0:
            span = int(span * 0.7)
            continue

        limiar = projecao.max() * limiar_frac
        candidatos = np.where(projecao > limiar)[0]
        if len(candidatos) < 2:
            span = int(span * 0.7)
            continue

        grupos = [[candidatos[0]]]
        for c in candidatos[1:]:
            if c - grupos[-1][-1] <= min_dist:
                grupos[-1].append(c)
            else:
                grupos.append([c])

        picos = [(int(np.mean(g)), float(projecao[g].max())) for g in grupos]
        if len(picos) >= 2:
            return picos, span, projecao, linhas
        span = int(span * 0.7)

    return [], span, ultima_proj, ultima_img


def detectar_crop_box(imagem_para_deteccao, margem=8, modo='so_vertical',
                      span_v_inicial_frac=0.5, span_h_inicial_frac=0.5,
                      span_minimo_frac=0.15, dilatar=True):
    """Detecta crop box (retorna coordenadas em pixels E fracoes)."""
    altura, largura = imagem_para_deteccao.shape[:2]
    centro_y, centro_x = altura // 2, largura // 2

    cinza = cv2.cvtColor(imagem_para_deteccao, cv2.COLOR_BGR2GRAY)
    cinza = cv2.GaussianBlur(cinza, (3, 3), 0)
    _, binario = cv2.threshold(cinza, 0, 255,
                               cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    if dilatar:
        kernel_d = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binario = cv2.dilate(binario, kernel_d, iterations=1)

    span_v_ini = int(altura * span_v_inicial_frac)
    span_h_ini = int(largura * span_h_inicial_frac)
    span_v_min = max(int(altura * span_minimo_frac), 30)
    span_h_min = max(int(largura * span_minimo_frac), 30)

    if modo in ('auto', 'so_vertical'):
        picos_v, span_v_usado, proj_v, linhas_v_img = _detectar_linhas_em_eixo(
            binario, 'vertical', span_v_ini, span_v_min)
    else:
        picos_v, span_v_usado, proj_v, linhas_v_img = [], 0, np.zeros(largura), np.zeros_like(binario)

    if modo in ('auto', 'so_horizontal'):
        picos_h, span_h_usado, proj_h, linhas_h_img = _detectar_linhas_em_eixo(
            binario, 'horizontal', span_h_ini, span_h_min)
    else:
        picos_h, span_h_usado, proj_h, linhas_h_img = [], 0, np.zeros(altura), np.zeros_like(binario)

    def melhor_pico(picos, lado, centro, margem_centro=30):
        if lado == 'menor':
            cands = [(p, f) for p, f in picos if p < centro - margem_centro]
        else:
            cands = [(p, f) for p, f in picos if p > centro + margem_centro]
        return max(cands, key=lambda x: x[1])[0] if cands else None

    if modo == 'so_vertical':
        y1, y2 = 0, altura
    else:
        ya = melhor_pico(picos_h, 'menor', centro_y)
        yb = melhor_pico(picos_h, 'maior', centro_y)
        y1 = (ya + margem) if ya is not None else 0
        y2 = (yb - margem) if yb is not None else altura

    if modo == 'so_horizontal':
        x1, x2 = 0, largura
    else:
        xe = melhor_pico(picos_v, 'menor', centro_x)
        xd = melhor_pico(picos_v, 'maior', centro_x)
        x1 = (xe + margem) if xe is not None else 0
        x2 = (xd - margem) if xd is not None else largura

    suc_v = (modo == 'so_horizontal') or (x1 > 0 and x2 < largura and x2 > x1 + 50)
    suc_h = (modo == 'so_vertical') or (y1 > 0 and y2 < altura and y2 > y1 + 50)
    sucesso = suc_v and suc_h and (x2 > x1 + 50) and (y2 > y1 + 50)

    info = {
        'sucesso': sucesso,
        'picos_v': [p for p, _ in picos_v],
        'picos_h': [p for p, _ in picos_h],
        'span_v_usado': span_v_usado,
        'span_h_usado': span_h_usado,
        'crop_box_px': (y1, y2, x1, x2),
        'crop_box_frac': (y1/altura, y2/altura, x1/largura, x2/largura),
        'dim_deteccao': (altura, largura),
    }
    return info


def recortar_em_resolucao_cheia(caminho_imagem, fator_deteccao=0.25,
                                margem=8, modo='so_vertical',
                                span_v_inicial_frac=0.5,
                                span_h_inicial_frac=0.5,
                                span_minimo_frac=0.15, dilatar=True):
    """
    Pipeline em 2 etapas:
    1. Detecta crop box em versao reduzida (rapido)
    2. Aplica crop na imagem original em resolucao cheia (preserva qualidade)
    """
    imagem_cheia = cv2.imread(str(caminho_imagem))
    if imagem_cheia is None:
        # Resgate para caminhos com acentos no Windows.
        imagem_cheia = imread_fallback(caminho_imagem)
    if imagem_cheia is None:
        return None, None

    h_cheia, w_cheia = imagem_cheia.shape[:2]

    img_reduzida = cv2.resize(imagem_cheia, None,
                              fx=fator_deteccao, fy=fator_deteccao,
                              interpolation=cv2.INTER_AREA)

    info = detectar_crop_box(
        img_reduzida,
        margem=margem,
        modo=modo,
        span_v_inicial_frac=span_v_inicial_frac,
        span_h_inicial_frac=span_h_inicial_frac,
        span_minimo_frac=span_minimo_frac,
        dilatar=dilatar,
    )

    if not info['sucesso']:
        del img_reduzida
        gc.collect()
        info['dim_original'] = (h_cheia, w_cheia)
        return imagem_cheia, info

    frac_y1, frac_y2, frac_x1, frac_x2 = info['crop_box_frac']
    y1_cheia = int(frac_y1 * h_cheia)
    y2_cheia = int(frac_y2 * h_cheia)
    x1_cheia = int(frac_x1 * w_cheia)
    x2_cheia = int(frac_x2 * w_cheia)

    recortada_cheia = imagem_cheia[y1_cheia:y2_cheia, x1_cheia:x2_cheia].copy()

    del imagem_cheia, img_reduzida
    gc.collect()

    info['crop_box_cheia_px'] = (y1_cheia, y2_cheia, x1_cheia, x2_cheia)
    info['dim_original'] = (h_cheia, w_cheia)
    info['dim_recortada'] = recortada_cheia.shape[:2]
    return recortada_cheia, info


# ---------------------------------------------------------------------------
# Worker de paralelismo (camada nova - nao altera a logica acima)
# ---------------------------------------------------------------------------
# Precisa ser uma funcao de modulo (top-level) para ser picklavel pelo
# ProcessPoolExecutor no Windows (start method = spawn).


def processar_foto(
    caminho_entrada: str | Path,
    pasta_saida: str | Path | None = None,
    formato: str | None = None,
    lote_id: str | None = None,
    fator_deteccao: float | None = None,
    margem: int | None = None,
    modo: str | None = None,
    span_v_inicial_frac: float | None = None,
    span_h_inicial_frac: float | None = None,
    span_minimo_frac: float | None = None,
    dilatar: bool | None = None,
) -> dict:
    """
    Recorta UMA foto e grava o quadrante em pasta_saida.

    Roda dentro de um worker: nunca levanta excecao para fora - qualquer
    problema volta no campo 'erro' do dicionario de resultado, para que uma
    foto ruim nao derrube o lote inteiro.
    """
    inicio = time.perf_counter()
    caminho_entrada = Path(caminho_entrada)
    pasta_saida = Path(pasta_saida or settings.PASTA_RECORTADAS)
    formato = formato or settings.RECORTE_FORMATO_SAIDA

    resultado = {
        "arquivo": caminho_entrada.name,
        "caminho_entrada": str(caminho_entrada),
        "saida": None,
        "sucesso": False,
        "deteccao_ok": False,
        "erro": None,
        "info": None,
        "duracao_seg": 0.0,
    }

    try:
        pasta_saida.mkdir(parents=True, exist_ok=True)

        recortada, info = recortar_em_resolucao_cheia(
            str(caminho_entrada),
            fator_deteccao=(settings.RECORTE_FATOR_DETECCAO
                            if fator_deteccao is None else fator_deteccao),
            margem=(settings.RECORTE_MARGEM if margem is None else margem),
            modo=(settings.RECORTE_MODO if modo is None else modo),
            span_v_inicial_frac=(settings.RECORTE_SPAN_V_INICIAL_FRAC
                                 if span_v_inicial_frac is None else span_v_inicial_frac),
            span_h_inicial_frac=(settings.RECORTE_SPAN_H_INICIAL_FRAC
                                 if span_h_inicial_frac is None else span_h_inicial_frac),
            span_minimo_frac=(settings.RECORTE_SPAN_MINIMO_FRAC
                              if span_minimo_frac is None else span_minimo_frac),
            dilatar=(settings.RECORTE_DILATAR if dilatar is None else dilatar),
        )

        if recortada is None:
            resultado["erro"] = "Imagem ilegivel (cv2.imread retornou None)"
            registrar_falha(caminho_entrada, "recorte", resultado["erro"],
                            lote_id=lote_id, mover_arquivo=True)
            return resultado

        resultado["info"] = info
        resultado["deteccao_ok"] = bool(info.get("sucesso"))

        if not info.get("sucesso"):
            motivo = (
                "Deteccao das linhas da grade falhou "
                f"(picos verticais encontrados: {len(info.get('picos_v', []))})"
            )
            if settings.RECORTE_FALHA_DETECCAO_E_ERRO:
                # Modo estrito: nao gera o quadrante; o stitching usara o
                # placeholder amarelo naquela celula.
                resultado["erro"] = motivo
                registrar_falha(caminho_entrada, "recorte", motivo, info,
                                lote_id=lote_id, mover_arquivo=True)
                return resultado
            # Comportamento validado no Colab: devolve a imagem CHEIA.
            logger.warning(
                "%s: %s - salvando a imagem CHEIA sem recorte.",
                caminho_entrada.name, motivo,
            )
            registrar_falha(caminho_entrada, "recorte_sem_deteccao", motivo, info,
                            lote_id=lote_id, mover_arquivo=False)

        caminho_saida = pasta_saida / f"{caminho_entrada.stem}{extensao_do_formato(formato)}"
        salvar_recortada(recortada, str(caminho_saida), formato=formato)

        resultado["saida"] = str(caminho_saida)
        resultado["sucesso"] = True
        logger.debug(
            "%s -> %s | original %s -> recortada %s",
            caminho_entrada.name, caminho_saida.name,
            info.get("dim_original"), info.get("dim_recortada"),
        )
    except Exception as exc:  # nenhuma foto pode derrubar o lote
        resultado["erro"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Erro ao recortar %s", caminho_entrada.name)
        registrar_falha(caminho_entrada, "recorte", resultado["erro"],
                        lote_id=lote_id, mover_arquivo=True)
    finally:
        resultado["duracao_seg"] = time.perf_counter() - inicio
        gc.collect()

    return resultado
