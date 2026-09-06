"""
Libera o candidato aprovado para fazer a avaliacao.

E o passo entre "o recrutador aprovou" e "o candidato pode responder a prova".
`liberar_avaliacao_click` atende o clique do recrutador, e
`_liberar_para_recrutamento` faz a parte que mexe no banco e nos cargos.

`SelecionarCandidatoLiberacaoView` aparece quando ha mais de um candidato
esperando: em vez de adivinhar, o bot pergunta qual deles liberar.
"""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import (
    CARGOS,
    prova_esta_suspensa,
)
from src.database.conexao import async_session
from src.database.models import Recrutamento
from src.recrutamento.aprovacao_panel import (
    EscolherCargoView,
    possui_cargo_recrutador_ou_superior,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.logger import log_mudanca_cargo
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_info,
    responder_view,
)


async def liberar_avaliacao_click(interaction: discord.Interaction):
    """Encaminha o recrutador à liberação da prova de um candidato.

    Busca somente os recrutamentos em estudo conduzidos pelo usuário atual. Se
    houver vários, envia um seletor no Discord; se houver um, avança direto.
    Essa escolha evita liberar por engano a prova de um candidato acompanhado
    por outro recrutador.
    """
    if not possui_cargo_recrutador_ou_superior(interaction.user):
        await responder_erro(
            interaction,
            titulo="Sem permissão",
            linhas=[
                "Você não possui permissão para liberar avaliações.",
            ],
        )
        return

    await interaction.response.defer(ephemeral=True)

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_recrutador == interaction.user.id,
                Recrutamento.status == "ESTUDANDO",
            )
        )
        recrutamentos_ativos = resultado.scalars().all()

    if not recrutamentos_ativos:
        await responder_aviso(
            interaction,
            titulo="Nada para mostrar",
            linhas=[
                "Você não possui nenhum candidato aguardando liberação de prova no "
                "momento.",
            ],
        )
        return

    if len(recrutamentos_ativos) == 1:
        await _liberar_para_recrutamento(interaction, recrutamentos_ativos[0])
        return

    # Mais de um candidato ativo: mostra um menu pra escolher qual liberar
    view = SelecionarCandidatoLiberacaoView(recrutamentos_ativos, interaction.guild)
    await responder_view(
        interaction,
        view,
        ephemeral=True,
    )


async def _liberar_para_recrutamento(
    interaction: discord.Interaction, recrutamento: Recrutamento
):
    guild = interaction.guild
    candidato = guild.get_member(recrutamento.discord_id_candidato)

    if candidato is None:
        await responder_erro(
            interaction,
            titulo="Não encontrado",
            linhas=[
                "Candidato não encontrado no servidor.",
            ],
        )
        return

    # Período de reabertura: sem prova — recrutador escolhe o cargo direto.
    if prova_esta_suspensa():
        nome_do_candidato = candidato.display_name
        view_cargo = EscolherCargoView(
            candidato_id=candidato.id,
            recrutamento_id=recrutamento.id,
            aprovador=interaction.user,
            mensagem_original=interaction.message,
            nome_do_candidato=nome_do_candidato,
        )
        await responder_view(
            interaction,
            view_cargo,
            ephemeral=True,
        )
        return

    cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
    cargo_prova = guild.get_role(CARGOS["PROVA"])

    await candidato.remove_roles(
        cargo_estudante, reason=f"Prova liberada por {interaction.user}"
    )
    await candidato.add_roles(
        cargo_prova, reason=f"Prova liberada por {interaction.user}"
    )

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(Recrutamento.id == recrutamento.id)
        )
        registro = resultado.scalar_one()
        registro.status = "PROVA_LIBERADA"
        await session.commit()

    await log_mudanca_cargo(
        guild,
        candidato=candidato,
        executor=interaction.user,
        cargos_removidos=[cargo_estudante.mention],
        cargos_adicionados=[cargo_prova.mention],
    )

    mensagem = (
        f"✅ Prova liberada para {candidato.mention}. Ele já pode iniciar a avaliação."
    )
    await responder_info(
        interaction,
        titulo="Prova liberada",
        linhas=[
            mensagem,
        ],
    )


class LinhaDeEscolhaDeQuemLiberar(discord.ui.ActionRow):
    """A lista suspensa com os candidatos que podem ser liberados agora."""

    def __init__(
        self,
        view_da_liberacao: SelecionarCandidatoLiberacaoView,
        opcoes_de_candidato: list[discord.SelectOption],
    ) -> None:
        super().__init__()

        self.view_da_liberacao = view_da_liberacao

        self.selecao_de_candidato = discord.ui.Select(
            placeholder="Selecione o candidato",
            options=opcoes_de_candidato,
        )
        self.selecao_de_candidato.callback = self.ao_escolher_candidato
        self.add_item(self.selecao_de_candidato)

    async def ao_escolher_candidato(self, interacao: discord.Interaction) -> None:
        """Libera a prova do candidato escolhido, sem perder o recrutamento dele."""
        await interacao.response.defer(ephemeral=True)

        id_do_recrutamento = int(self.selecao_de_candidato.values[0])
        recrutamento_escolhido = self.view_da_liberacao.recrutamentos_por_id[
            id_do_recrutamento
        ]
        await _liberar_para_recrutamento(interacao, recrutamento_escolhido)


class SelecionarCandidatoLiberacaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card privado para escolher qual candidato liberar para a prova.

    Aparece so quando o recrutador tem mais de um candidato em fase de estudo.
    Com um candidato so, o codigo libera direto e nem mostra este card — nao faz
    sentido pedir para escolher entre uma opcao.

    O texto de cada opcao usa o apelido do membro no servidor. Quando a pessoa
    saiu do Discord e nao ha mais apelido, mostra o ID, para a opcao nao ficar em
    branco e o recrutador ainda conseguir identificar a ficha.
    """

    def __init__(self, recrutamentos: list[Recrutamento], guild: discord.Guild):
        super().__init__(timeout=60)

        self.recrutamentos_por_id = {
            recrutamento.id: recrutamento for recrutamento in recrutamentos
        }

        opcoes_de_candidato: list[discord.SelectOption] = []
        for recrutamento in recrutamentos:
            membro_candidato = guild.get_member(recrutamento.discord_id_candidato)

            if membro_candidato is not None:
                nome_para_mostrar = membro_candidato.display_name
            else:
                nome_para_mostrar = f"ID {recrutamento.discord_id_candidato}"

            opcoes_de_candidato.append(
                discord.SelectOption(
                    label=nome_para_mostrar,
                    value=str(recrutamento.id),
                )
            )

        self.container_da_escolha = discord.ui.Container(
            accent_colour=discord.Color.blurple()
        )
        self.container_da_escolha.add_item(
            discord.ui.TextDisplay("## 📋 Liberar candidato para a prova")
        )
        self.container_da_escolha.add_item(
            discord.ui.TextDisplay(
                "Voce possui mais de um candidato em fase de estudo. "
                "Selecione qual deseja liberar para a prova."
            )
        )
        self.container_da_escolha.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        self.container_da_escolha.add_item(
            LinhaDeEscolhaDeQuemLiberar(self, opcoes_de_candidato)
        )

        self.add_item(self.container_da_escolha)
