"""
Testes do watcher.

O ponto critico coberto aqui e a NUMERACAO DAS ARMADILHAS. No modo grid cada
lote fechado e uma armadilha diferente, e o numero dela entra no nome de
todos os 80 quadrantes (VARD14A1..VARD14H10). Se dois lotes recebessem o
mesmo numero, o segundo sobrescreveria o primeiro no acervo - silenciosamente,
porque os nomes seriam identicos.
"""

from __future__ import annotations

import pytest

from scripts.watcher import proxima_armadilha


def test_primeiro_lote_usa_o_numero_informado():
    """Sem historico, vale o que o operador passou em --armadilha."""
    assert proxima_armadilha({}, 14) == 14


def test_cada_lote_fechado_avanca_para_a_proxima_armadilha():
    """O watcher vigia a pasta continuamente: um lote novo = outra armadilha."""
    estado = {"ultima_armadilha": 14}
    assert proxima_armadilha(estado, 14) == 15


def test_reinicio_do_watcher_nao_reusa_um_numero_ja_gravado():
    """
    Reiniciar com o mesmo --armadilha 14 depois de tres lotes tem que
    continuar em 17, e nao sobrescrever a armadilha 14 no acervo.
    """
    estado = {"ultima_armadilha": 16}
    assert proxima_armadilha(estado, 14) == 17


def test_numero_informado_a_frente_do_historico_vence():
    """Pular para a armadilha 20 e uma decisao do operador - o watcher obedece."""
    estado = {"ultima_armadilha": 16}
    assert proxima_armadilha(estado, 20) == 20


def test_estado_corrompido_nao_derruba_a_numeracao():
    """Estado antigo (sem o campo) ou com lixo cai no numero informado."""
    assert proxima_armadilha({"ultima_armadilha": None}, 14) == 14
    assert proxima_armadilha({"ultima_armadilha": "abc"}, 14) == 14


def test_watcher_no_grid_exige_o_numero_da_armadilha():
    """Ligar o watcher em grid sem --armadilha para na porta, nao no 1o lote."""
    from src.opcoes import OpcoesProcessamento

    with pytest.raises(ValueError, match="armadilha"):
        OpcoesProcessamento(modo="grid", armadilha=None)
