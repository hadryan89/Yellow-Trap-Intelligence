"""
Testes das linhas de comando.

Erro de PARAMETRO nao e defeito do programa: quem esqueceu `--armadilha`
precisa ler uma frase util e receber o codigo de saida 1 (falha estrutural,
o mesmo que pasta inexistente), e nao um traceback de 20 linhas que o n8n
registra como estouro inesperado.
"""

from __future__ import annotations

import pytest

from scripts import run_pipeline, watcher


# Trecho exclusivo do erro que estamos testando. "armadilha" sozinho nao
# serve: o resumo do lote ja traz uma linha "Armadilha" para a COR da
# placa (auto/amarela/azul), que nao tem nada a ver com este parametro.
FRASE = "exige o numero da armadilha"


def _rodar(modulo, argv, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [modulo.__name__] + argv)
    codigo = modulo.main()
    return codigo, capsys.readouterr()


def test_grid_sem_armadilha_sai_com_erro_legivel(pastas_isoladas, monkeypatch,
                                                 capsys):
    codigo, saida = _rodar(run_pipeline, ["--modo", "grid", "--simular"],
                           monkeypatch, capsys)

    assert codigo == 1
    texto = saida.out + saida.err
    assert FRASE in texto
    assert "Traceback" not in texto


def test_watcher_no_grid_sem_armadilha_sai_com_erro_legivel(pastas_isoladas,
                                                            monkeypatch,
                                                            capsys):
    codigo, saida = _rodar(watcher, ["--modo", "grid", "--uma-vez"],
                           monkeypatch, capsys)

    assert codigo == 1
    texto = saida.out + saida.err
    assert FRASE in texto
    assert "Traceback" not in texto


@pytest.mark.parametrize("argv", [
    ["--modo", "sequencial", "--simular"],
    ["--modo", "recorte", "--simular"],
])
def test_modos_fora_do_grid_nao_pedem_armadilha(pastas_isoladas, monkeypatch,
                                                capsys, argv):
    """So o grid nomeia por quadrante - os outros seguem sem o parametro."""
    codigo, saida = _rodar(run_pipeline, argv, monkeypatch, capsys)

    # Lote vazio: o pipeline aborta por falta de foto, nao por falta de
    # armadilha. O que importa aqui e que o parametro nao foi cobrado.
    assert FRASE not in (saida.out + saida.err)
    assert codigo == 1
