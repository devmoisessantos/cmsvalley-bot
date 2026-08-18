"""
Confere e aplica a whitelist: apelido, cargo e log.

`processar_whitelist` e a porta de entrada. Ela grava a ficha, muda o apelido do
membro para o padrao do servidor e avisa no canal de log.

`_formatar_telefone` deixa todos os telefones com a mesma cara, senao cada
pessoa escreveria de um jeito e a busca depois nao acharia ninguem.

`_aplicar_apelido` pode falhar sem ser culpa do bot: o Discord nao deixa mudar o
apelido de quem tem cargo mais alto que ele. Nesse caso a whitelist continua
valendo, e o log registra que o apelido ficou como estava.
"""

import logging
import re

import discord

from src.config import CANAIS, CARGOS
from src.utils.log_container import LogContainerView
from src.utils.logger import log_mudanca_cargo
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)

registrador = logging.getLogger(__name__)


def _aplicar_apelido(
    candidato: discord.Member, nome: str, sobrenome: str, id_fivem: str
) -> str:
    """
    Monta o apelido no formato 'Nome S. | idFivem' ou 'Nome Sobrenome | idFivem'
    respeitando o limite de 32 caracteres.

    Regras:
    - Se o nome completo (nome + sobrenome) + sufixo for <= 32, usa completo
    - Se for > 32, abrevia o sobrenome para a primeira letra + ponto
    - Se ainda assim for > 32, trunca o nome
    """
    sufixo = f" | {id_fivem}"
    limite_total = 32 - len(sufixo)  # Espaço disponível para nome e sobrenome

    # Capitaliza o nome e sobrenome
    nome_capitalizado = nome[:1].upper() + nome[1:].lower() if nome else ""
    sobrenome_capitalizado = (
        sobrenome[:1].upper() + sobrenome[1:].lower() if sobrenome else ""
    )

    # Tenta usar nome completo primeiro
    nome_completo = f"{nome_capitalizado} {sobrenome_capitalizado}"

    if len(nome_completo) <= limite_total:
        return f"{nome_completo}{sufixo}"

    # Se passou do limite, abrevia o sobrenome
    nome_abreviado = f"{nome_capitalizado} {sobrenome_capitalizado[:1]}."

    if len(nome_abreviado) <= limite_total:
        return f"{nome_abreviado}{sufixo}"

    # Se ainda passou, trunca o nome
    nome_truncado = nome_abreviado[:limite_total]
    return f"{nome_truncado}{sufixo}"


def _formatar_telefone(telefone: str) -> str:
    """Remove qualquer caractere não-numérico e insere o traço no formato XXX-XXX."""
    apenas_numeros = re.sub(r"\D", "", telefone)
    if len(apenas_numeros) == 6:
        return f"{apenas_numeros[:3]}-{apenas_numeros[3:]}"
    return telefone  # formato inesperado, mantém como foi digitado


async def processar_whitelist(
    interaction: discord.Interaction,
    *,
    nome: str,
    sobrenome: str,
    idade: str,
    telefone: str,
    id_fivem: str,
):
    """Libera o acesso após validar a identidade e registrar a conclusão.

    Impede uma segunda liberação pelo cargo de Visitantes e rejeita idade ou ID
    FiveM inválidos. Ao concluir, tenta ajustar o apelido, adiciona o cargo no
    Discord, envia os logs administrativos e confirma o resultado ao usuário.
    """
    await interaction.response.defer(ephemeral=True)

    candidato = interaction.user
    guild = interaction.guild
    cargo_visitante = guild.get_role(CARGOS["Visitantes"])

    if cargo_visitante in candidato.roles:
        await responder_erro(
            interaction,
            titulo="WhiteList já feita",
            linhas=[
                "Você já realizou a Whitelist anteriormente.",
            ],
        )
        return

    if not idade.isdigit():
        await responder_erro(
            interaction,
            titulo="Sem permissão",
            linhas=[
                "Idade inválida. Digite apenas números.",
            ],
        )
        return

    if not id_fivem.isdigit() or len(id_fivem) > 6:
        await responder_erro(
            interaction,
            titulo="Dado inválido",
            linhas=[
                "Identificador (ID FiveM) inválido. Deve conter no máximo 6 dígitos "
                "numéricos.",
            ],
        )
        return

    telefone_formatado = _formatar_telefone(telefone)
    # 🔥 Aplica o apelido com nome e sobrenome
    apelido = _aplicar_apelido(
        candidato=candidato, nome=nome, sobrenome=sobrenome, id_fivem=id_fivem
    )

    # 🔥 Tenta aplicar o apelido e guarda o resultado
    apelido_aplicado = False
    try:
        await candidato.edit(nick=apelido)
        apelido_aplicado = True
    except discord.Forbidden:
        registrador.error(f"❌ Sem permissão para alterar apelido de {candidato.name}")
    except Exception as erro:
        registrador.error(f"❌ Erro ao aplicar apelido: {erro}")

    await candidato.add_roles(cargo_visitante, reason="Whitelist concluída")

    await log_mudanca_cargo(
        guild,
        candidato=candidato,
        executor=guild.me,
        cargos_adicionados=[cargo_visitante.mention],
    )

    await _enviar_log_whitelist(
        guild,
        candidato=candidato,
        nome=nome,
        sobrenome=sobrenome,
        idade=idade,
        telefone=telefone_formatado,
        id_fivem=id_fivem,
        cargo_visitante=cargo_visitante,
        apelido=apelido,
        apelido_aplicado=apelido_aplicado,  # ✅ Adicionado
    )

    await responder_sucesso(
        interaction,
        titulo="WhiteList concluída",
        linhas=[
            "Whitelist concluída com sucesso! Seu acesso foi liberado.",
        ],
    )


async def _enviar_log_whitelist(
    guild: discord.Guild,
    *,
    candidato: discord.Member,
    nome: str,
    sobrenome: str,
    idade: str,
    telefone: str,
    id_fivem: str,
    cargo_visitante: discord.Role,
    apelido: str,
    apelido_aplicado: bool,
):
    canal = guild.get_channel(CANAIS["LOG_WHITELIST"])
    if canal is None:
        return

    linhas = (
        f"➜ **Usuário:** {candidato.mention} (`{candidato.id}`)\n"
        f"➜ **Nome:** `{nome}`\n"
        f"➜ **Sobrenome:** `{sobrenome}`\n"
        f"➜ **Idade:** `{idade}`\n"
        f"➜ **Telefone:** `{telefone}`\n"
        f"➜ **Identificador:** `{id_fivem}`\n"
        f"➜ **Cargos adicionados:** {cargo_visitante.mention} "
        f"(`{cargo_visitante.id}`)\n"
        f"➜ **Nome base (USERNAME):** `{candidato.name}`\n"
        f"➜ **Apelido aplicado:** `{apelido}`"
    )

    if not apelido_aplicado:
        linhas += (
            f"\n- **Não foi possível redefinir o apelido para** `{candidato.name}` ❌"
        )

    view = LogContainerView(
        titulo="✅ Sistema de Liberação - Sucesso",
        linhas=linhas,
        guild=guild,
        cor=discord.Color.green(),
        avatar_url=candidato.display_avatar.url,
    )
    await canal.send(view=view)
