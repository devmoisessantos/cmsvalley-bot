# src/utils/transacao.py
"""
Transacao segura no banco de dados.

Por que este arquivo existe:

Quando uma operacao escreve em mais de uma tabela (por exemplo aplicar uma
punicao = gravar a punicao + tirar o cargo + gravar o log), se der erro no
meio do caminho, metade das gravacoes ficaria salva e a outra metade nao.
O banco ficaria inconsistente e ninguem saberia.

Este helper resolve isso: se qualquer passo falhar, ele desfaz tudo
(`rollback`) e deixa o banco exatamente como estava antes.

Como usar:

    from src.utils.transacao import transacao_segura

    async with transacao_segura() as sessao_do_banco:
        sessao_do_banco.add(nova_punicao)
        sessao_do_banco.add(novo_log)
        # nao precisa chamar commit: o helper confirma sozinho no fim
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.conexao import async_session


@asynccontextmanager
async def transacao_segura():
    """
    Abre uma sessao do banco, confirma no fim e desfaz tudo se algo falhar.

    Use sempre que a operacao gravar em mais de uma tabela.

    O que acontece na pratica:

    1. Abre uma sessao nova do banco.
    2. Entrega essa sessao para o seu bloco `async with`.
    3. Se o seu bloco terminar sem erro, chama `commit` e confirma tudo.
    4. Se o seu bloco levantar qualquer excecao, chama `rollback`, registra o
       aviso no log e deixa a excecao subir para quem chamou tratar.

    O `rollback` nao e opcional: sem ele, a sessao fica em estado de transacao
    quebrada e a proxima consulta que pegar essa conexao do pool falha tambem.
    """
    async with async_session() as sessao_do_banco:
        try:
            yield sessao_do_banco
            await sessao_do_banco.commit()
        except Exception as erro_da_transacao:
            await sessao_do_banco.rollback()
            logging.error(
                "Transacao desfeita (rollback) por causa de um erro: %s: %s",
                type(erro_da_transacao).__name__,
                erro_da_transacao,
            )
            raise


async def confirmar_com_rollback(sessao_do_banco: AsyncSession) -> None:
    """
    Confirma uma sessao que ja esta aberta, desfazendo tudo se o commit falhar.

    Use quando o codigo ja abriu a sessao do jeito antigo
    (`async with async_session() as sessao`) e voce so quer garantir que uma
    falha no commit nao deixe a sessao quebrada.

    Em codigo novo, prefira o `transacao_segura` acima, que ja faz isso sozinho.
    """
    try:
        await sessao_do_banco.commit()
    except Exception as erro_do_commit:
        await sessao_do_banco.rollback()
        logging.error(
            "Commit desfeito (rollback) por causa de um erro: %s: %s",
            type(erro_do_commit).__name__,
            erro_do_commit,
        )
        raise
