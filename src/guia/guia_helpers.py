# src/guia/guia_helpers.py
"""
Helpers compartilhados do domínio guia.

Montagem de links de canal, botões e linhas de ActionRow
para os painéis de boas-vindas e tutoriais.
"""

from __future__ import annotations

import discord

from src.config import (
    CANAIS,
    GUILD_ID,
)


def buscar_id_do_canal(chave_do_canal: str) -> int:
    """
    Devolve o ID do canal configurado em CANAIS.

    Retorna 0 se a chave não existir ou estiver vazia.
    """
    id_do_canal = CANAIS.get(chave_do_canal) or 0
    return int(id_do_canal)


def montar_url_do_canal(chave_do_canal: str) -> str | None:
    """
    Monta a URL discord.com/channels/... para a chave informada.

    Retorna None se o canal ainda não estiver configurado.
    """
    id_do_canal = buscar_id_do_canal(chave_do_canal)
    canal_esta_configurado = id_do_canal > 0
    if not canal_esta_configurado:
        return None

    url_do_canal = f"https://discord.com/channels/{GUILD_ID}/{id_do_canal}"
    return url_do_canal


def montar_botao_link(
    rotulo: str,
    chave_do_canal: str,
    emoji: str = "🔗",
) -> discord.ui.Button | None:
    """
    Cria um botão de link para o canal da chave.

    Retorna None se o canal não estiver configurado.
    """
    url_do_canal = montar_url_do_canal(chave_do_canal)
    if url_do_canal is None:
        return None

    botao_de_link = discord.ui.Button(
        label=rotulo,
        style=discord.ButtonStyle.link,
        url=url_do_canal,
        emoji=emoji,
    )
    return botao_de_link


def montar_linha_de_botoes_link(
    botoes: list[dict],
) -> discord.ui.ActionRow | None:
    """
    Monta uma ActionRow com até 5 botões de link.

    Cada item de `botoes` deve ter:
    - rotulo: texto do botão
    - chave_do_canal: chave em CANAIS

    Botões sem canal configurado são ignorados.
    Se nenhum botão válido sobrar, retorna None.
    """
    if not botoes:
        return None

    linha_dos_botoes = discord.ui.ActionRow()
    quantidade_adicionada = 0

    for dados_do_botao in botoes[:5]:
        rotulo_do_botao = dados_do_botao["rotulo"]
        chave_do_canal = dados_do_botao["chave_do_canal"]

        botao_de_link = montar_botao_link(
            rotulo=rotulo_do_botao,
            chave_do_canal=chave_do_canal,
        )
        if botao_de_link is None:
            continue

        linha_dos_botoes.add_item(botao_de_link)
        quantidade_adicionada += 1

    nenhum_botao_foi_adicionado = quantidade_adicionada == 0
    if nenhum_botao_foi_adicionado:
        return None

    return linha_dos_botoes


def montar_thumbnail_do_servidor(
    guilda: discord.Guild,
) -> discord.ui.Thumbnail | None:
    """Devolve o Thumbnail com o ícone da guilda, ou None se não houver ícone."""
    guilda_tem_icone = guilda.icon is not None
    if not guilda_tem_icone:
        return None

    return discord.ui.Thumbnail(url=guilda.icon.url)
