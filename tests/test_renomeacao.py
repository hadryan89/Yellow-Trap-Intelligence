"""
Testes do protocolo 1 (nomeacao das imagens).

Pontos criticos:
  * ordem natural (img2 antes de img10) - se a ordem quebrar, todo o de-para
    sai trocado;
  * a copia tem que ser byte-a-byte identica (MD5 conferido);
  * o plano sequencial (VARD0, VARD1, ...) nao pode ter teto de quantidade;
  * a estrategia 'virtual' nao pode escrever nada em disco;
  * o ZIP tem que ser ZIP_STORED (sem recompressao).
"""

from __future__ import annotations

import zipfile

import pytest

from config import settings
from src.renomeacao import (
    aplicar_plano,
    calcular_hash_md5,
    criar_zip_sem_compressao,
    executar_renomeacao,
    gerar_mapeamento,
    mapear_sequencial,
    natural_key,
    planejar_nomeacao,
    proximo_indice_sequencial,
    renomear_com_verificacao,
    verificar_integridade_zip,
)


def _criar_fotos(pasta, nomes) -> list:
    """Cria arquivos com conteudo unico (o protocolo 1 nao decodifica imagem)."""
    caminhos = []
    for indice, nome in enumerate(nomes):
        caminho = pasta / nome
        caminho.write_bytes(b"CONTEUDO-" + str(indice).encode() + b"\x00" * 64)
        caminhos.append(caminho)
    return caminhos


# ---------------------------------------------------------------------------
# Ordenacao natural
# ---------------------------------------------------------------------------


def test_ordem_natural_coloca_img2_antes_de_img10():
    nomes = ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg", "IMG_20.jpg"]
    assert sorted(nomes, key=natural_key) == [
        "IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg", "IMG_20.jpg",
    ]


def test_ordem_natural_ignora_caixa():
    assert sorted(["b1.jpg", "A2.jpg"], key=natural_key) == ["A2.jpg", "b1.jpg"]


# ---------------------------------------------------------------------------
# Mapeamento
# ---------------------------------------------------------------------------


def test_mapeamento_segue_a_ordem_do_grid(tmp_path):
    nomes = [f"DSC{i:04d}.JPG" for i in range(1, 41)]
    _criar_fotos(tmp_path, nomes)

    mapeamento, total_arquivos, total_alvos = gerar_mapeamento(
        tmp_path, settings.LETRAS_COLUNAS, settings.NUMEROS_LINHAS
    )

    assert total_arquivos == 40 and total_alvos == 40
    assert mapeamento[0] == ("DSC0001.JPG", "a1.jpg")
    assert mapeamento[9] == ("DSC0010.JPG", "a10.jpg")
    assert mapeamento[10] == ("DSC0011.JPG", "b1.jpg")
    assert mapeamento[-1] == ("DSC0040.JPG", "d10.jpg")


def test_mapeamento_normaliza_a_extensao_para_minusculo(tmp_path):
    _criar_fotos(tmp_path, ["FOTO1.JPG", "FOTO2.JPEG"])
    mapeamento, _, _ = gerar_mapeamento(tmp_path, ["a"], [1, 2])
    assert mapeamento == [("FOTO1.JPG", "a1.jpg"), ("FOTO2.JPEG", "a2.jpeg")]


def test_mapeamento_com_menos_fotos_que_posicoes(tmp_path):
    _criar_fotos(tmp_path, [f"IMG_{i}.jpg" for i in range(1, 6)])
    mapeamento, total_arquivos, total_alvos = gerar_mapeamento(
        tmp_path, settings.LETRAS_COLUNAS, settings.NUMEROS_LINHAS
    )
    assert total_arquivos == 5 and total_alvos == 40
    assert len(mapeamento) == 5
    assert mapeamento[-1][1] == "a5.jpg"


def test_mapeamento_ignora_extensoes_nao_suportadas(tmp_path):
    _criar_fotos(tmp_path, ["IMG_1.jpg", "notas.txt", "planilha.xlsx"])
    mapeamento, total_arquivos, _ = gerar_mapeamento(tmp_path, ["a"], [1, 2])
    assert total_arquivos == 1
    assert mapeamento == [("IMG_1.jpg", "a1.jpg")]


# ---------------------------------------------------------------------------
# Plano sequencial (VARD0, VARD1, ...)
# ---------------------------------------------------------------------------


def test_sequencial_usa_o_padrao_sem_zeros_a_esquerda(tmp_path):
    """O default e VARD0, VARD1, VARD2 - contador cru, comecando em zero."""
    caminhos = _criar_fotos(tmp_path, ["DSC1.JPG", "DSC2.jpg", "DSC3.png"])
    itens = mapear_sequencial(caminhos)

    assert [i.nome_novo for i in itens] == ["VARD0.jpg", "VARD1.jpg", "VARD2.png"]
    assert itens[0].stem_novo == "VARD0"


def test_sequencial_aceita_largura_fixa_se_pedida(tmp_path):
    """`digitos` continua disponivel para quem quiser nomes alinhados."""
    caminhos = _criar_fotos(tmp_path, ["DSC1.JPG", "DSC2.jpg", "DSC3.png"])
    itens = mapear_sequencial(caminhos, prefixo="VARD", digitos=7, inicio=1)

    assert [i.nome_novo for i in itens] == [
        "VARD0000001.jpg", "VARD0000002.jpg", "VARD0000003.png",
    ]
    assert itens[0].stem_novo == "VARD0000001"


def test_sequencial_nao_tem_teto_de_quantidade(tmp_path):
    """O grid para em 40; o sequencial precisa aguentar o lote inteiro."""
    nomes = [f"IMG_{i:05d}.jpg" for i in range(1, 2001)]
    caminhos = [tmp_path / nome for nome in nomes]  # sem tocar no disco

    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)

    assert len(plano) == 2000
    assert plano.ignorados == []
    # Sem largura fixa o contador so cresce - nada e truncado em 4 digitos.
    assert plano.itens[0].nome_novo == "VARD0.jpg"
    assert plano.itens[-1].nome_novo == "VARD1999.jpg"


def test_sequencial_respeita_o_indice_inicial(tmp_path):
    caminhos = _criar_fotos(tmp_path, ["a.jpg", "b.jpg"])
    itens = mapear_sequencial(caminhos, inicio=41)
    assert [i.nome_novo for i in itens] == ["VARD41.jpg", "VARD42.jpg"]


def test_proximo_indice_le_o_maior_ja_existente(tmp_path):
    _criar_fotos(tmp_path, ["VARD0.png", "VARD42.png", "outro.png"])
    assert proximo_indice_sequencial(tmp_path) == 43


def test_proximo_indice_entende_o_acervo_antigo_com_zeros(tmp_path):
    """Lotes gravados antes, no formato VARD0000042, continuam sendo lidos."""
    _criar_fotos(tmp_path, ["VARD0000042.png", "VARD7.png"])
    assert proximo_indice_sequencial(tmp_path) == 43


def test_proximo_indice_em_pasta_vazia_usa_o_inicio(tmp_path):
    assert proximo_indice_sequencial(tmp_path, inicio=1) == 1
    assert proximo_indice_sequencial(tmp_path / "nao_existe", inicio=7) == 7


def test_plano_do_grid_marca_o_que_ficou_de_fora(tmp_path):
    caminhos = _criar_fotos(tmp_path, [f"IMG_{i}.jpg" for i in range(1, 6)])
    plano = planejar_nomeacao(caminhos, modo=settings.MODO_GRID,
                              letras=["a"], numeros=[1, 2])

    assert len(plano) == 2
    assert plano.total_arquivos == 5
    assert plano.ignorados == ["IMG_3.jpg", "IMG_4.jpg", "IMG_5.jpg"]


def test_modo_recorte_preserva_o_nome(tmp_path):
    caminhos = _criar_fotos(tmp_path, ["FOTO_A.JPG"])
    plano = planejar_nomeacao(caminhos, modo=settings.MODO_RECORTE)
    assert plano.itens[0].nome_novo == "FOTO_A.JPG"


# ---------------------------------------------------------------------------
# Estrategias de materializacao
# ---------------------------------------------------------------------------


def test_estrategia_virtual_nao_escreve_nada(tmp_path):
    """O ganho de escala vem daqui: renomear sem duplicar o lote em disco."""
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    caminhos = _criar_fotos(origem, ["IMG_1.jpg", "IMG_2.jpg"])

    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)
    resultado = aplicar_plano(plano, destino,
                              estrategia=settings.ESTRATEGIA_VIRTUAL)

    assert not destino.exists()
    assert resultado.itens == [
        (str(origem / "IMG_1.jpg"), "VARD0"),
        (str(origem / "IMG_2.jpg"), "VARD1"),
    ]


def test_estrategia_hardlink_nao_duplica_conteudo(tmp_path):
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    caminhos = _criar_fotos(origem, ["IMG_1.jpg"])

    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)
    aplicar_plano(plano, destino, estrategia=settings.ESTRATEGIA_HARDLINK)

    alvo = destino / "VARD0.jpg"
    assert alvo.read_bytes() == (origem / "IMG_1.jpg").read_bytes()


def test_estrategia_mover_esvazia_a_origem(tmp_path):
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    caminhos = _criar_fotos(origem, ["IMG_1.jpg"])

    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)
    aplicar_plano(plano, destino, estrategia=settings.ESTRATEGIA_MOVER)

    assert not (origem / "IMG_1.jpg").exists()
    assert (destino / "VARD0.jpg").exists()


def test_materializacao_em_paralelo_preserva_a_ordem(tmp_path):
    """As threads terminam fora de ordem; a fila de saida nao pode embaralhar."""
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    caminhos = _criar_fotos(origem, [f"IMG_{i:03d}.jpg" for i in range(1, 31)])

    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)
    resultado = aplicar_plano(plano, destino,
                              estrategia=settings.ESTRATEGIA_COPIAR, workers=8)

    assert [stem for _, stem in resultado.itens] == [
        f"VARD{i}" for i in range(30)
    ]
    assert len(resultado.hashes) == 30
    assert resultado.falhas == []


def test_arquivo_ilegivel_nao_derruba_a_materializacao(tmp_path):
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    caminhos = _criar_fotos(origem, ["IMG_1.jpg", "IMG_2.jpg"])
    plano = planejar_nomeacao(caminhos, modo=settings.MODO_SEQUENCIAL)
    (origem / "IMG_1.jpg").unlink()  # some antes da copia

    resultado = aplicar_plano(plano, destino,
                              estrategia=settings.ESTRATEGIA_COPIAR,
                              registrar_falhas=False)

    assert len(resultado.falhas) == 1
    assert resultado.falhas[0]["arquivo"] == "IMG_1.jpg"
    assert [stem for _, stem in resultado.itens] == ["VARD1"]


# ---------------------------------------------------------------------------
# Copia com verificacao MD5
# ---------------------------------------------------------------------------


def test_copia_e_byte_a_byte_identica(tmp_path):
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    _criar_fotos(origem, [f"IMG_{i}.jpg" for i in range(1, 41)])

    mapeamento, _, _ = gerar_mapeamento(origem, settings.LETRAS_COLUNAS,
                                        settings.NUMEROS_LINHAS)
    hashes, falhas = renomear_com_verificacao(origem, destino, mapeamento)

    assert falhas == []
    assert len(hashes) == 40
    for original, novo in mapeamento:
        assert (destino / novo).read_bytes() == (origem / original).read_bytes()
        assert hashes[novo] == calcular_hash_md5(origem / original)


def test_original_permanece_intacto(tmp_path):
    """A renomeacao COPIA - a pasta de entrada nao pode ser esvaziada."""
    origem = tmp_path / "origem"
    origem.mkdir()
    _criar_fotos(origem, ["IMG_1.jpg", "IMG_2.jpg"])

    mapeamento, _, _ = gerar_mapeamento(origem, ["a"], [1, 2])
    renomear_com_verificacao(origem, tmp_path / "destino", mapeamento)

    assert (origem / "IMG_1.jpg").exists()
    assert (origem / "IMG_2.jpg").exists()


def test_destino_e_limpo_antes_de_copiar(tmp_path):
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()
    (destino / "lixo_do_lote_anterior.png").write_bytes(b"antigo")
    _criar_fotos(origem, ["IMG_1.jpg"])

    mapeamento, _, _ = gerar_mapeamento(origem, ["a"], [1])
    renomear_com_verificacao(origem, destino, mapeamento, limpar_destino=True)

    assert not (destino / "lixo_do_lote_anterior.png").exists()
    assert (destino / "a1.jpg").exists()


def test_md5_e_calculado_em_chunks(tmp_path):
    """Arquivo maior que o chunk de 8192 bytes tem que bater com o hashlib."""
    import hashlib

    caminho = tmp_path / "grande.bin"
    dados = bytes(range(256)) * 200  # ~51 KB
    caminho.write_bytes(dados)
    assert calcular_hash_md5(caminho) == hashlib.md5(dados).hexdigest()


# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------


def test_zip_usa_modo_stored(tmp_path):
    pasta = tmp_path / "renomeadas"
    pasta.mkdir()
    _criar_fotos(pasta, ["a1.jpg", "a2.jpg"])
    caminho_zip = tmp_path / "lote.zip"

    criar_zip_sem_compressao(pasta, caminho_zip)

    with zipfile.ZipFile(caminho_zip) as zf:
        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.compress_size == info.file_size


def test_verificacao_do_zip_confirma_os_hashes(tmp_path):
    pasta = tmp_path / "renomeadas"
    pasta.mkdir()
    caminhos = _criar_fotos(pasta, ["a1.jpg", "a2.jpg"])
    hashes = {c.name: calcular_hash_md5(c) for c in caminhos}
    caminho_zip = tmp_path / "lote.zip"

    criar_zip_sem_compressao(pasta, caminho_zip)
    todos_ok, resultados = verificar_integridade_zip(caminho_zip, hashes)

    assert todos_ok is True
    assert all(resultados.values())


def test_verificacao_do_zip_detecta_divergencia(tmp_path):
    pasta = tmp_path / "renomeadas"
    pasta.mkdir()
    caminhos = _criar_fotos(pasta, ["a1.jpg"])
    caminho_zip = tmp_path / "lote.zip"
    criar_zip_sem_compressao(pasta, caminho_zip)

    hashes_errados = {caminhos[0].name: "0" * 32}
    todos_ok, resultados = verificar_integridade_zip(caminho_zip, hashes_errados)

    assert todos_ok is False
    assert resultados["a1.jpg"] is False


# ---------------------------------------------------------------------------
# Etapa completa
# ---------------------------------------------------------------------------


def test_executar_renomeacao_ponta_a_ponta(pastas_isoladas, monkeypatch):
    monkeypatch.setattr(settings, "RENOMEACAO_CRIAR_ZIP", True)
    monkeypatch.setattr(settings, "RENOMEACAO_VERIFICAR_ZIP", True)
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _criar_fotos(entrada, [f"IMG_{i:03d}.jpg" for i in range(1, 41)])

    relatorio = executar_renomeacao(lote_id="LOTE_TESTE")

    assert relatorio["total_arquivos"] == 40
    assert len(relatorio["hashes"]) == 40
    assert relatorio["falhas"] == []
    assert relatorio["zip_ok"] is True
    assert (pastas_isoladas["PASTA_RENOMEADAS"] / "a1.jpg").exists()
    assert (pastas_isoladas["PASTA_RENOMEADAS"] / "d10.jpg").exists()


def test_executar_renomeacao_sem_fotos_levanta(pastas_isoladas):
    with pytest.raises(ValueError, match="Nenhuma imagem"):
        executar_renomeacao()


def test_executar_renomeacao_com_pasta_inexistente_levanta(pastas_isoladas,
                                                           tmp_path):
    with pytest.raises(FileNotFoundError):
        executar_renomeacao(pasta_origem=tmp_path / "nao_existe")
