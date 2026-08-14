"""
Testes de integracao: as 3 etapas encadeadas + paralelismo + falhas.

Nota sobre paralelismo nos testes: no Windows o ProcessPoolExecutor usa
'spawn', e os workers reimportam config.settings do zero - por isso o
monkeypatch das pastas nao chega neles. Os testes que dependem de pastas
isoladas rodam com num_workers=1 (caminho sequencial, mesmo codigo); ha um
teste dedicado exercitando o caminho multiprocesso de verdade.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from config import settings
from src.paralelismo import executar_em_paralelo, resolver_num_workers
from src.pipeline import executar_pipeline_completo
from src.recorte import processar_foto


def _povoar_entrada(pasta, foto_modelo, quantidade=40, prefixo="IMG"):
    """Copia a mesma foto sintetica N vezes com nomes sequenciais."""
    dados = foto_modelo.read_bytes()
    for i in range(1, quantidade + 1):
        (pasta / f"{prefixo}_{i:03d}.png").write_bytes(dados)


# ---------------------------------------------------------------------------
# Paralelismo
# ---------------------------------------------------------------------------


def test_resolver_num_workers_deixa_um_core_livre(monkeypatch):
    monkeypatch.setattr(settings, "NUM_WORKERS", None)
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert resolver_num_workers() == 7
    assert resolver_num_workers(3) == 3
    assert resolver_num_workers(0) == 1  # nunca zero


def test_paralelismo_preserva_a_ordem_dos_resultados(tmp_path, foto_valida):
    """A lista de saida tem que seguir a ordem da lista de entrada."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=6, prefixo="ORD")
    entradas = sorted(str(p) for p in tmp_path.glob("ORD_*.png"))
    saida = tmp_path / "recortes"

    resultados = executar_em_paralelo(
        processar_foto, entradas, num_workers=2, descricao="teste",
        pasta_saida=str(saida), formato="png",
    )

    assert [r["arquivo"] for r in resultados] == [p.split("\\")[-1] for p in entradas]
    assert all(r["sucesso"] for r in resultados)
    assert len(list(saida.glob("*.png"))) == 6


def test_uma_foto_ruim_nao_derruba_o_lote(tmp_path, foto_valida):
    """Requisito: o pipeline nunca aborta por causa de 1 foto."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=3, prefixo="OK")
    ruim = tmp_path / "OK_004.png"
    ruim.write_bytes(b"nao sou uma imagem")
    entradas = sorted(str(p) for p in tmp_path.glob("OK_*.png"))

    resultados = executar_em_paralelo(
        processar_foto, entradas, num_workers=2, descricao="teste",
        pasta_saida=str(tmp_path / "recortes"), formato="png",
    )

    assert len(resultados) == 4
    assert sum(1 for r in resultados if r["sucesso"]) == 3
    falha = next(r for r in resultados if not r["sucesso"])
    assert falha["arquivo"] == "OK_004.png"
    assert falha["erro"]


def test_lista_vazia_nao_quebra():
    assert executar_em_paralelo(processar_foto, [], num_workers=2) == []


# ---------------------------------------------------------------------------
# Pipeline completo
# ---------------------------------------------------------------------------


@pytest.mark.lento
def test_pipeline_completo_ponta_a_ponta(pastas_isoladas, foto_valida):
    """40 fotos entram brutas e sai uma placa 4x10 exportada."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=40)

    sumario = executar_pipeline_completo(lote_id="LOTE_TESTE", num_workers=1)

    assert sumario.sucesso is True
    assert sumario.total_entrada == 40
    assert sumario.renomeadas == 40
    assert sumario.recortadas_ok == 40
    assert sumario.recortadas_sem_deteccao == 0
    assert sumario.placeholders == 0
    assert sumario.quadrantes_no_stitching == 40

    # 02_renomeadas: a1..d10
    renomeadas = pastas_isoladas["PASTA_RENOMEADAS"]
    assert (renomeadas / "a1.png").exists() and (renomeadas / "d10.png").exists()

    # 03_recortadas: 40 quadrantes
    assert len(list(pastas_isoladas["PASTA_RECORTADAS"].glob("*.png"))) == 40

    # 04_placas_montadas/<lote>/: placa 4 linhas x 10 colunas
    saida = pastas_isoladas["PASTA_PLACAS"] / "LOTE_TESTE"
    placa = cv2.imread(str(saida / "placa_LOSSLESS.png"))
    altura_quadrante, largura_quadrante = 800, 336  # crop conhecido da fixture
    assert placa.shape[:2] == (4 * altura_quadrante, 10 * largura_quadrante)
    assert (saida / "placa_CIENTIFICO.tiff").exists()
    assert (saida / "sumario.json").exists()


@pytest.mark.lento
def test_pipeline_com_lote_incompleto_usa_placeholder(pastas_isoladas, foto_valida):
    """38 fotos: as 2 celulas restantes viram placeholder amarelo."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=38)

    sumario = executar_pipeline_completo(lote_id="LOTE_38", num_workers=1)

    assert sumario.sucesso is True
    assert sumario.renomeadas == 38
    assert sumario.placeholders == 2
    # d9 e d10 sao as ultimas posicoes do grid
    faltantes = [f["arquivo"] for f in sumario.falhas if f["etapa"] == "stitching"]
    assert faltantes == ["d9", "d10"]

    placa = cv2.imread(str(pastas_isoladas["PASTA_PLACAS"] / "LOTE_38"
                           / "placa_LOSSLESS.png"))
    altura_celula = placa.shape[0] // 4
    largura_celula = placa.shape[1] // 10
    canto = placa[3 * altura_celula + altura_celula // 2,
                  9 * largura_celula + largura_celula // 2]
    assert tuple(int(v) for v in canto) == settings.STITCHING_COR_PLACEHOLDER


@pytest.mark.lento
def test_recortes_limpos_entre_lotes(pastas_isoladas, foto_valida, monkeypatch):
    """Sobras de um lote anterior nao podem entrar no stitching do proximo."""
    monkeypatch.setattr(settings, "LIMPAR_PASTAS_INTERMEDIARIAS", True)
    sobra = pastas_isoladas["PASTA_RECORTADAS"] / "c3.png"
    cv2.imwrite(str(sobra), np.zeros((10, 10, 3), dtype=np.uint8))

    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=5)
    sumario = executar_pipeline_completo(lote_id="LOTE_5", num_workers=1)

    assert sumario.quadrantes_no_stitching == 5
    assert not sobra.exists() or sumario.placeholders == 35


def test_pipeline_sem_fotos_reporta_falha_sem_explodir(pastas_isoladas):
    """Pasta vazia: o lote falha de forma controlada, com sumario."""
    sumario = executar_pipeline_completo(lote_id="LOTE_VAZIO", num_workers=1)
    assert sumario.sucesso is False
    assert sumario.total_falhas >= 1
    assert sumario.falhas[-1]["etapa"] == "pipeline"


def test_sumario_serializa_para_json(pastas_isoladas, foto_valida, tmp_path):
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=2)
    sumario = executar_pipeline_completo(lote_id="LOTE_JSON", num_workers=1)

    import json

    caminho = sumario.salvar_json(tmp_path / "s.json")
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    assert dados["lote_id"] == "LOTE_JSON"
    assert "duracao_seg" in dados and "falhas" in dados
