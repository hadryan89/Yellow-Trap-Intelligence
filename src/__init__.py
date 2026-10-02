"""
YellowTrap Pipeline - processamento de imagens de armadilhas amarelas.

Grupo Progresso / Setor de Inovacao.

O pipeline tem tres etapas: nomeacao, recorte e montagem das placas.

Modulos:
    opcoes      - OpcoesProcessamento: o contrato de entrada de uma execucao
    renomeacao  - Etapa 1: plano de nomes (grid VARD14A1 ou VARD1) e sua
                  materializacao (virtual / hardlink / copiar+MD5 / mover)
    recorte     - Etapa 2: recorta o quadrante central (deteccao de grade)
    montagem    - Etapa 3: junta os quadrantes de 40 em 40 no formato da placa
    exportacao  - gravacao dos quadrantes e das placas (multiplas resolucoes)
    paralelismo - wrapper de ProcessPoolExecutor com submissao em janela
    pipeline    - orquestracao das tres etapas
    utils       - logging, medicao de recursos, registro de falhas, sumario
"""

__version__ = "2.1.0"
