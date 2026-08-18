"""
Card de aprovar ou reprovar um candidato, e a escolha do cargo final.

`AprovacaoView` e o card que o recrutador ve com a ficha do candidato e os
botoes de decisao. `montar_container_resultado` desenha o resultado depois da
decisao tomada.

`possui_cargo_recrutador_ou_superior` esta aqui e nao em utils porque a regra e
so deste fluxo: aprovar gente e uma acao sensivel, e a conferencia precisa
acontecer antes de qualquer botao funcionar.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
)
from src.database.conexao import async_session
from src.database.models import (
    Recrutamento,
    Usuario,
)
from src.recrutamento.recrutamento_class import NovoRecrutamento
from src.utils.error_handling import LoggingViewMixin, ignorar_falha_cosmetica
from src.utils.logger import (
    log_decisao,
    log_mudanca_cargo,
)
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
    responder_view,
)
from src.utils.nickname import aplicar_prefixo

registrador = logging.getLogger(__name__)


def possui_cargo_recrutador_ou_superior(membro: discord.Member) -> bool:
    """Confirma permissão para decidir avaliações pela hierarquia configurada."""
    ids_permitidos = {
        CARGOS["✈️・Recrutador"],
        CARGOS["🥼・Instrutor"],
        CARGOS["👑・SUPERVISOR"],
        CARGOS["👑・VICE DIRETOR"],
        CARGOS["👑・DIRETOR"],
        CARGOS["🔍・COORDENADOR"],
        CARGOS["👑 |  VICE DIRETOR GERAL"],
        CARGOS["👑 |  DIRETOR GERAL"],
        CARGOS["👑 | RESPONSÁVEL GERAL"],
    }
    return any(cargo.id in ids_permitidos for cargo in membro.roles)


def montar_container_resultado(
    candidato: discord.Member,
    nota: float,
    acertos: int,
    total: int,
    respostas_erradas: list[int],
    detalhes_erros: list[dict],
    status_emoji: str,
    guild: discord.Guild,
    cor: discord.Color,
) -> discord.ui.Container:
    """Monta o cartão visual com o resultado detalhado da avaliação.

    Exibe a identificação do candidato, seu desempenho e, quando houver, cada
    resposta incorreta. Os detalhes evitam que a decisão da equipe pareça
    arbitrária e permitem revisar a avaliação sem consultar outros registros.
    """

    linhas = (
        f"- **Candidato:** {candidato.mention} (`{candidato.id}`)\n"
        f"- **Acertos:** {acertos}/{total} (`{nota}%`)\n"
        f"- **Perguntas erradas:** "
        f"{respostas_erradas if respostas_erradas else 'Nenhuma'}\n"
        f"- **Status:** {status_emoji}"
    )

    agora = int(datetime.now(timezone.utc).timestamp())
    rodape_texto = f"-# {guild.name} • <t:{agora}:f>"

    componentes = [
        discord.ui.TextDisplay("# 📋 Resultado da Avaliação\n\n"),
        discord.ui.Section(
            linhas, accessory=discord.ui.Thumbnail(candidato.display_avatar.url)
        ),
    ]

    if detalhes_erros:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        bloco_erros = "> # 📋 Perguntas erradas:\n\n"
        for erro in detalhes_erros:
            bloco_erros += (
                f"**Pergunta {erro['numero']}** ( {erro['enunciado']} )\n"
                f"✖ Sua resposta: {erro['resposta_dada']}\n"
                f"✔ Correta: {erro['resposta_correta']}\n\n"
            )

        componentes.append(discord.ui.TextDisplay(bloco_erros.strip()))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(
        discord.ui.TextDisplay(rodape_texto)
    )  # <- corrigido: TextDisplay em vez de string pura

    return discord.ui.Container(*componentes, accent_color=cor)


class AprovacaoView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        candidato: discord.Member,
        recrutamento_id: int,
        nota: float,
        acertos: int,
        total: int,
        respostas_erradas: list[int],
        detalhes_erros: list[dict],
        status_emoji: str,
        guild: discord.Guild,
        cor: discord.Color,
    ):
        super().__init__(timeout=None)
        self.candidato_id = candidato.id
        self.recrutamento_id = recrutamento_id

        container = montar_container_resultado(
            candidato,
            nota,
            acertos,
            total,
            respostas_erradas,
            detalhes_erros,
            status_emoji,
            guild,
            cor,
        )
        self.action_row = discord.ui.ActionRow()

        self.botao_aprovar = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            custom_id="aprovacao:aprovar",
        )
        self.botao_reprovar = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            custom_id="aprovacao:reprovar",
        )
        self.botao_aprovar.callback = self.aprovar
        self.botao_reprovar.callback = self.reprovar
        self.action_row.add_item(self.botao_aprovar)
        self.action_row.add_item(self.botao_reprovar)

        self.add_item(container)
        self.add_item(self.action_row)

    async def _travar_botoes(self, interaction: discord.Interaction):
        """Desativa os botões imediatamente, evitando duplo clique enquanto processa."""
        self.botao_aprovar.disabled = True
        self.botao_reprovar.disabled = True
        await interaction.message.edit(view=self)

    async def aprovar(self, interaction: discord.Interaction):
        """Inicia a escolha do cargo para uma aprovação autorizada.

        Confere a permissão do usuário e bloqueia os botões antes de abrir o
        seletor de cargo. Esse bloqueio evita decisões duplicadas enquanto a
        próxima etapa ainda está sendo exibida no Discord.
        """
        if not possui_cargo_recrutador_ou_superior(interaction.user):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    "Você não possui permissão para aprovar candidatos.",
                ],
            )
            return

        candidato = interaction.guild.get_member(self.candidato_id)
        if candidato is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado no servidor.",
                ],
            )
            return

        await self._travar_botoes(interaction)  # desativa antes de abrir o select

        view_cargo = EscolherCargoView(
            candidato_id=self.candidato_id,
            recrutamento_id=self.recrutamento_id,
            aprovador=interaction.user,
            mensagem_original=interaction.message,
            nome_do_candidato=candidato.mention,
        )
        await responder_view(
            interaction,
            view_cargo,
            ephemeral=True,
        )

    async def reprovar(self, interaction: discord.Interaction):
        """Conclui a reprovação, removendo acessos temporários do candidato.

        Após validar a permissão, remove os cargos de prova, grava o resultado
        e o status de visitante no banco e envia mensagens e logs no Discord.
        Os botões são travados para impedir que a mesma avaliação seja decidida
        duas vezes.
        """
        if not possui_cargo_recrutador_ou_superior(interaction.user):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    "Você não possui permissão para reprovar candidatos.",
                ],
            )
            return

        await interaction.response.defer()
        # ... resto da lógica de reprovação continua igual, terminando com view=None

        guild = interaction.guild
        candidato = guild.get_member(self.candidato_id)

        cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
        cargo_prova = guild.get_role(CARGOS["PROVA"])
        cargos_para_remover = [
            cargo
            for cargo in [cargo_estudante, cargo_prova]
            if cargo in candidato.roles
        ]
        if cargos_para_remover:
            await candidato.remove_roles(
                *cargos_para_remover, reason=f"Reprovado por {interaction.user}"
            )
            await log_mudanca_cargo(
                guild,
                candidato=candidato,
                executor=interaction.user,
                cargos_removidos=[cargo.mention for cargo in cargos_para_remover],
            )

        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento).where(Recrutamento.id == self.recrutamento_id)
            )
            recrutamento = resultado.scalar_one()
            recrutamento.status = "REPROVADO"
            recrutamento.data_fim = datetime.now(
                timezone.utc
            )  # antes: datetime.now(timezone.utc)

            resultado_usuario = await session.execute(
                select(Usuario).where(Usuario.discord_id == self.candidato_id)
            )
            usuario = resultado_usuario.scalar_one()
            usuario.status = "VISITANTE"

            usuario.data_ultima_reprovacao = datetime.now(
                timezone.utc
            )  # antes: datetime.now(timezone.utc)
            await session.commit()

        await self._travar_botoes(interaction)  # desativa antes de abrir o select

        async def excluir_mensagem(mensagem, delay=60):
            """Aguarda o tempo especificado e exclui a mensagem"""
            await asyncio.sleep(delay)
            try:
                await mensagem.delete()
            except discord.NotFound as erro_em_excluir_mensagem:
                # Enfeite que falhou: excluir mensagem.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_excluir_mensagem,
                    o_que_falhou="excluir mensagem",
                )
            except discord.Forbidden:
                registrador.error("Sem permissão para excluir a mensagem")

        # O card fica 60 segundos na tela: e tempo suficiente para o recrutador
        # conferir a decisao antes de a mensagem se apagar sozinha.
        await responder_erro(
            interaction,
            titulo="Candidato reprovado",
            linhas=[
                f"{candidato.mention} foi reprovado por {interaction.user.mention}.",
            ],
            delay=60,
        )

        await log_decisao(
            guild,
            CANAIS["LOG_REPROVACOES"],
            titulo="❌ Candidato Reprovado",
            candidato=candidato,
            executor=interaction.user,
            cargo=f"{cargo_prova.mention} / {cargo_estudante.mention} foram removidos",
            extra=f"Nota: {recrutamento.nota_percentual}% | Pode tentar novamente em 24h",
            cor=discord.Color.red(),
        )


class LinhaDeEscolhaDoCargoInicial(discord.ui.ActionRow):
    """A lista suspensa com os dois cargos de entrada do hospital."""

    def __init__(self, view_do_cargo: EscolherCargoView) -> None:
        super().__init__()
        self.view_do_cargo = view_do_cargo

    @discord.ui.select(
        placeholder="Selecione o cargo inicial",
        options=[
            discord.SelectOption(label="Enfermeiro(a)", value="🔰・Enfermeiro (a)"),
            discord.SelectOption(label="Paramédico", value="🚑・Paramédico"),
        ],
    )
    async def ao_escolher_cargo(
        self,
        interacao: discord.Interaction,
        selecao_de_cargo: discord.ui.Select,
    ) -> None:
        """Repassa o cargo escolhido para a view que sabe aplicar a aprovacao."""
        await self.view_do_cargo.escolher(interacao, selecao_de_cargo)


class EscolherCargoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card privado onde o recrutador escolhe com qual cargo o candidato entra.

    Aplicar a aprovacao mexe em varias coisas de uma vez: tira os cargos
    temporarios, coloca os definitivos, muda o apelido, grava no banco e escreve
    no log. Por isso a escolha fica num card separado e some em 60 segundos: e
    uma acao sensivel, nao um menu para deixar aberto.
    """

    def __init__(
        self,
        candidato_id: int,
        recrutamento_id: int,
        aprovador: discord.Member,
        mensagem_original: discord.Message,
        nome_do_candidato: str = "o candidato",
    ):
        super().__init__(timeout=60)
        self.candidato_id = candidato_id
        self.recrutamento_id = recrutamento_id
        self.aprovador = aprovador
        self.mensagem_original = mensagem_original

        self.container_da_escolha = discord.ui.Container(
            accent_colour=discord.Color.brand_green()
        )
        self.container_da_escolha.add_item(
            discord.ui.TextDisplay("## ✅ Cargo inicial")
        )
        self.container_da_escolha.add_item(
            discord.ui.TextDisplay(f"Escolha o cargo inicial para {nome_do_candidato}.")
        )
        self.container_da_escolha.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        self.container_da_escolha.add_item(LinhaDeEscolhaDoCargoInicial(self))

        self.add_item(self.container_da_escolha)

    async def escolher(
        self, interaction: discord.Interaction, item: discord.ui.Select
    ) -> None:
        """Aplica a aprovação usando o cargo selecionado pelo recrutador.

        Atualiza cargos e apelido do candidato, persiste o resultado no banco e
        publica os registros e o próximo painel no Discord. A combinação de
        cargos especiais também preserva a base hierárquica de Paramédico.
        """
        await interaction.response.defer()

        cargo_escolhido = item.values[0]
        guild = interaction.guild
        candidato = guild.get_member(self.candidato_id)

        cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
        cargo_prova = guild.get_role(CARGOS["PROVA"])
        cargo_hp = guild.get_role(CARGOS["HP S・Valley"])
        cargo_aprovado = guild.get_role(CARGOS["Aprovado"])
        cargo_visitante = guild.get_role(CARGOS["Visitantes"])
        cargo_final = guild.get_role(CARGOS[cargo_escolhido])

        cargos_remover = [
            cargo
            for cargo in [cargo_estudante, cargo_prova, cargo_visitante]
            if cargo in candidato.roles
        ]
        if cargos_remover:
            await candidato.remove_roles(
                *cargos_remover, reason=f"Aprovado por {self.aprovador}"
            )

        cargos_adicionar = [cargo_final, cargo_hp, cargo_aprovado]
        # Paramédico entra com Enfermeiro(a) junto (hierarquia base)
        if cargo_escolhido == "🚑・Paramédico":
            cargo_enfermeiro = guild.get_role(CARGOS.get("🔰・Enfermeiro (a)", 0) or 0)
            if (
                cargo_enfermeiro is not None
                and cargo_enfermeiro not in cargos_adicionar
            ):
                cargos_adicionar.append(cargo_enfermeiro)

        cargos_adicionar = [cargo for cargo in cargos_adicionar if cargo is not None]
        await candidato.add_roles(
            *cargos_adicionar,
            reason=f"Aprovado por {self.aprovador}",
        )

        await log_mudanca_cargo(
            guild,
            candidato=candidato,
            executor=self.aprovador,
            cargos_removidos=[cargo.mention for cargo in cargos_remover],
            cargos_adicionados=[cargo.mention for cargo in cargos_adicionar],
        )

        novo_nickname = aplicar_prefixo(candidato.display_name, cargo_escolhido)
        try:
            await candidato.edit(nick=novo_nickname)
        except discord.Forbidden as erro_em_escolher:
            # Enfeite que falhou: avisar o candidato por mensagem direta.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_escolher,
                o_que_falhou="avisar o candidato por mensagem direta",
            )

        # 👇 PRIMEIRO busca/atualiza o recrutamento no banco — só depois disso ele existe
        # como variável
        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento).where(Recrutamento.id == self.recrutamento_id)
            )
            recrutamento = resultado.scalar_one()
            recrutamento.status = "APROVADO"
            recrutamento.cargo_final = cargo_escolhido
            recrutamento.data_fim = datetime.now(timezone.utc)

            resultado_usuario = await session.execute(
                select(Usuario).where(Usuario.discord_id == self.candidato_id)
            )
            usuario = resultado_usuario.scalar_one()
            usuario.status = "APROVADO"
            usuario.ja_foi_aprovado = True
            await session.commit()

        # 👇 SÓ AGORA, depois do "async with" acima, "recrutamento" pode ser usado
        await log_decisao(
            guild,
            CANAIS["LOG_APROVACOES"],
            titulo="✅ Candidato Aprovado",
            candidato=candidato,
            executor=self.aprovador,
            cargo=f"{cargo_final.mention}, {cargo_hp.mention}, {cargo_aprovado.mention}",
            extra=f"Nota: `{recrutamento.nota_percentual}%`",
            cor=discord.Color.green(),
        )

        canal_recrutamentos = guild.get_channel(CANAIS["RECRUTAMENTOS"])
        if canal_recrutamentos:
            await canal_recrutamentos.send(
                view=NovoRecrutamento(
                    candidato=candidato,
                    recrutador=self.aprovador,
                    cargo_role=cargo_final,
                    id_fivem=recrutamento.id_fivem,
                    guild=guild,
                )
            )

        async def excluir_mensagem(mensagem, delay=25):
            """Aguarda o tempo especificado e exclui a mensagem"""
            await asyncio.sleep(delay)
            try:
                await mensagem.delete()
            except discord.NotFound as erro_em_excluir_mensagem:
                # Enfeite que falhou: excluir mensagem.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_excluir_mensagem,
                    o_que_falhou="excluir mensagem",
                )
            except discord.Forbidden:
                registrador.error("Sem permissão para excluir a mensagem")

        # O card fica 25 segundos na tela antes de se apagar sozinho.
        await responder_sucesso(
            interaction,
            titulo="Candidato aprovado",
            linhas=[
                f"**Aprovado como {cargo_final.mention}** por {self.aprovador.mention}",
            ],
            delay=25,
        )
