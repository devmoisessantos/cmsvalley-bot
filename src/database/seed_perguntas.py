# src/database/seed_perguntas.py
"""
Perguntas da prova de recrutamento.

- seed_perguntas_se_vazio: usado na subida do bot (só preenche se a tabela estiver
vazia)
- seed: script manual para inserir de novo (via python -m)
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import (
    func,
    select,
)

from src.database.conexao import (
    async_session,
    init_db,
)
from src.database.models import Pergunta

registrador = logging.getLogger(__name__)

# Cada item: (enunciado, lista de opções, letra da resposta correta)
PERGUNTAS = [
    (
        "É permitido desligar o toggle apenas para evitar ser saqueado?",
        [
            "Sim, é uma estratégia válida",
            "Não, é proibido usar o toggle para evitar saque",
            "Depende do horário",
        ],
        "B",
    ),
    (
        "Qual é a punição para assédio, intolerância ou discriminação?",
        [
            "Apenas um aviso verbal",
            "Punição imediata",
            "Nada, desde que seja fora do HP",
        ],
        "B",
    ),
    (
        "Ao sair para atendimento EXTERNO, em qual rádio você deve estar?",
        ["Rádio 12", "Rádio 13", "Qualquer rádio"],
        "B",
    ),
    (
        "Qual dessas ações é PROIBIDA durante o serviço?",
        [
            "Carregar pessoas sem autorização (comando H)",
            "Usar estetoscópio de cor diferente",
            "Usar óculos",
        ],
        "A",
    ),
    (
        "É permitido guardar itens pessoais no baú do hospital?",
        [
            "Sim, se estiverem identificados",
            "Sim, desde que não atrapalhe",
            "Não, é proibido colocar itens pessoais no baú do HP",
        ],
        "C",
    ),
    (
        "Sobre o uniforme, o que é PERMITIDO?",
        [
            "Qualquer máscara e chapéu exagerado",
            "Óculos, chapéu sem exageros, estetoscópio de cor diferente e sapatos brancos",
            "Modificar o uniforme livremente",
        ],
        "B",
    ),
    (
        "Você pode reanimar um paciente fora da enfermaria (saguão/hall)?",
        [
            "Não, somente dentro da enfermaria",
            "Sim, é permitido; mas se for cobrar, leve o paciente para a enfermaria",
            "Sim, e pode cobrar no próprio saguão",
        ],
        "B",
    ),
    (
        "Qual é o valor padrão de reanimação em atendimento EXTERNO?",
        ["8.000", "10.000", "16.000"],
        "C",
    ),
    (
        "O que fazer em caso de ausência prolongada?",
        [
            "Apenas avisar no rádio",
            "Registrar no canal de registrar-ausência",
            "Não é necessário avisar",
        ],
        "B",
    ),
    (
        "É permitido aceitar propina (dinheiro ou itens)?",
        [
            "Sim, se for de paciente",
            "Sim, se for fora de serviço",
            "Não, é totalmente proibido",
        ],
        "C",
    ),
    (
        "Ao prosseguir, você confirma que leu e concorda com todas as regras do CMS VALLEY.",
        ["Estou ciente e concordo com todas as regras", "Não concordo"],
        "A",
    ),
]


async def _inserir_perguntas(sessao) -> int:
    """Insere todas as perguntas da lista PERGUNTAS. Retorna quantas foram inseridas."""
    for ordem, (enunciado, opcoes, resposta_correta) in enumerate(PERGUNTAS, start=1):
        sessao.add(
            Pergunta(
                ordem=ordem,
                enunciado=enunciado,
                opcoes=json.dumps(opcoes, ensure_ascii=False),
                resposta_correta=resposta_correta,
            )
        )
    await sessao.commit()
    return len(PERGUNTAS)


async def seed_perguntas_se_vazio():
    """
    Se a tabela de perguntas estiver vazia, preenche com as perguntas padrão.

    Chamado automaticamente na subida do bot.
    """
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(func.count()).select_from(Pergunta)
        )
        total_de_perguntas = resultado_consulta.scalar_one()

        if total_de_perguntas > 0:
            return

        quantidade_inserida = await _inserir_perguntas(sessao)
        registrador.info(
            f"{quantidade_inserida} perguntas inseridas automaticamente "
            "(tabela estava vazia)."
        )


async def seed():
    """
    Script manual: cria tabelas e insere as perguntas.

    Uso: python -m src.database.seed_perguntas
    Atenção: não verifica se já existem — pode duplicar se a tabela não estiver vazia.
    """
    await init_db()
    async with async_session() as sessao:
        quantidade_inserida = await _inserir_perguntas(sessao)
    registrador.info(f"{quantidade_inserida} perguntas foram inseridas com sucesso.")


if __name__ == "__main__":
    asyncio.run(seed())
