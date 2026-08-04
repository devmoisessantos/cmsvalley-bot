import discord
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select

from src.config import CARGOS, CANAIS
from src.database.connection import async_session
from src.database.models import Usuario, Recrutamento
from src.utils.logger import log_mudanca_cargo
from src.utils.nickname import aplicar_prefixo
from src.utils.error_handling import LoggingViewMixin
from src.panels.log_recrutamento import NovoRecrutamento, NovoRecrutamentoLog
from src.utils.logger import log_decisao

def possui_cargo_recrutador_ou_superior(membro: discord.Member) -> bool:
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

    linhas = (
        f"- **Candidato:** {candidato.mention} (`{candidato.id}`)\n"
        f"- **Acertos:** {acertos}/{total} (`{nota}%`)\n"
        f"- **Perguntas erradas:** {respostas_erradas if respostas_erradas else 'Nenhuma'}\n"
        f"- **Status:** {status_emoji}"
    )

    agora = int(datetime.now(timezone.utc).timestamp())
    rodape_texto = f"-# {guild.name} • <t:{agora}:f>"

    componentes = [
        discord.ui.TextDisplay("# 📋 Resultado da Avaliação\n\n"),
        discord.ui.Section(linhas, accessory=discord.ui.Thumbnail(candidato.display_avatar.url)),
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
    componentes.append(discord.ui.TextDisplay(rodape_texto))  # <- corrigido: TextDisplay em vez de string pura

    return discord.ui.Container(*componentes, accent_color=cor)

class AprovacaoView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, candidato: discord.Member, recrutamento_id: int, nota: float,
                 acertos: int, total: int, respostas_erradas: list[int],
                 detalhes_erros: list[dict], status_emoji: str, guild: discord.Guild, cor: discord.Color):
        super().__init__(timeout=None)
        self.candidato_id = candidato.id
        self.recrutamento_id = recrutamento_id

        container = montar_container_resultado(
            candidato, nota, acertos, total, respostas_erradas, detalhes_erros, status_emoji, guild, cor
        )
        self.action_row = discord.ui.ActionRow()

        self.botao_aprovar = discord.ui.Button(label="Aprovar", style=discord.ButtonStyle.success, custom_id="aprovacao:aprovar")
        self.botao_reprovar = discord.ui.Button(label="Reprovar", style=discord.ButtonStyle.danger, custom_id="aprovacao:reprovar")
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
        if not possui_cargo_recrutador_ou_superior(interaction.user):
            await interaction.response.send_message(
                "❌ Você não possui permissão para aprovar candidatos.", ephemeral=True
            )
            return

        candidato = interaction.guild.get_member(self.candidato_id)
        if candidato is None:
            await interaction.response.send_message("❌ Membro não encontrado no servidor.", ephemeral=True)
            return

        await self._travar_botoes(interaction)  # desativa antes de abrir o select

        view_cargo = EscolherCargoView(
            candidato_id=self.candidato_id,
            recrutamento_id=self.recrutamento_id,
            aprovador=interaction.user,
            mensagem_original=interaction.message,
        )
        await interaction.response.send_message(
            f"Escolha o cargo inicial para {candidato.mention}:", view=view_cargo, ephemeral=True
        )

    async def reprovar(self, interaction: discord.Interaction):
        if not possui_cargo_recrutador_ou_superior(interaction.user):
            await interaction.response.send_message(
                "❌ Você não possui permissão para reprovar candidatos.", ephemeral=True
            )
            return

        await interaction.response.defer()
        # ... resto da lógica de reprovação continua igual, terminando com view=None

        guild = interaction.guild
        candidato = guild.get_member(self.candidato_id)

        cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
        cargo_prova = guild.get_role(CARGOS["PROVA"])
        cargos_para_remover = [c for c in [cargo_estudante, cargo_prova] if c in candidato.roles]
        if cargos_para_remover:
            await candidato.remove_roles(*cargos_para_remover, reason=f"Reprovado por {interaction.user}")
            await log_mudanca_cargo(
                guild, candidato=candidato, executor=interaction.user,
                cargos_removidos=[c.mention for c in cargos_para_remover],
            )

        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento).where(Recrutamento.id == self.recrutamento_id)
            )
            recrutamento = resultado.scalar_one()
            recrutamento.status = "REPROVADO"
            recrutamento.data_fim = datetime.now(timezone.utc)  # antes: datetime.now(timezone.utc)

            resultado_usuario = await session.execute(
                select(Usuario).where(Usuario.discord_id == self.candidato_id)
            )
            usuario = resultado_usuario.scalar_one()
            usuario.status = "VISITANTE"
             
            usuario.data_ultima_reprovacao = datetime.now(timezone.utc)    # antes: datetime.now(timezone.utc)
            await session.commit()

        await self._travar_botoes(interaction)  # desativa antes de abrir o select
        

        async def excluir_mensagem(mensagem, delay=60):
            """Aguarda o tempo especificado e exclui a mensagem"""
            await asyncio.sleep(delay)
            try:
                await mensagem.delete()
            except discord.NotFound:
                pass  # Mensagem já foi excluída
            except discord.Forbidden:
                print("Sem permissão para excluir a mensagem")

        # No seu comando:
        mensagem = await interaction.followup.send(
            f"❌ {candidato.mention} foi reprovado por {interaction.user.mention}.",
            ephemeral=True
        )

        # Agenda a exclusão em 1 minutos (60 segundos)
        asyncio.create_task(excluir_mensagem(mensagem, 60))

        await log_decisao(
            guild, CANAIS["LOG_REPROVACOES"],
            titulo="❌ Candidato Reprovado",
            candidato=candidato, 
            executor=interaction.user,
            cargo=f"{cargo_prova.mention} / {cargo_estudante.mention} foram removidos",
            extra=f"Nota: {recrutamento.nota_percentual}% | Pode tentar novamente em 24h",
            cor=discord.Color.red(),
        )
class EscolherCargoView(LoggingViewMixin, discord.ui.View):
    def __init__(self, candidato_id: int, recrutamento_id: int, aprovador: discord.Member,
                 mensagem_original: discord.Message):
        super().__init__(timeout=60)
        self.candidato_id = candidato_id
        self.recrutamento_id = recrutamento_id
        self.aprovador = aprovador
        self.mensagem_original = mensagem_original

    @discord.ui.select(
        placeholder="Selecione o cargo inicial",
        options=[
            discord.SelectOption(label="Enfermeiro(a)", value="🔰・Enfermeiro (a)"),
            discord.SelectOption(label="Paramédico", value="🚑・Paramédico"),
        ],
    )

    async def escolher(self, interaction: discord.Interaction, item: discord.ui.Select):
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

        cargos_remover = [c for c in [cargo_estudante, cargo_prova, cargo_visitante] if c in candidato.roles]
        if cargos_remover:
            await candidato.remove_roles(*cargos_remover, reason=f"Aprovado por {self.aprovador}")

        await candidato.add_roles(cargo_final, cargo_hp, cargo_aprovado, reason=f"Aprovado por {self.aprovador}")

        await log_mudanca_cargo(
            guild, candidato=candidato, executor=self.aprovador,
            cargos_removidos=[c.mention for c in cargos_remover],
            cargos_adicionados=[cargo_final.mention, cargo_hp.mention, cargo_aprovado.mention],
        )

        novo_nickname = aplicar_prefixo(candidato.display_name, cargo_escolhido)
        try:
            await candidato.edit(nick=novo_nickname)
        except discord.Forbidden:
            pass

        # 👇 PRIMEIRO busca/atualiza o recrutamento no banco — só depois disso ele existe como variável
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
            guild, CANAIS["LOG_APROVACOES"],
            titulo="✅ Candidato Aprovado",
            candidato=candidato, executor=self.aprovador,
            cargo=f"{cargo_final.mention}, {cargo_hp.mention}, {cargo_aprovado.mention}",
            extra=f"Nota: `{recrutamento.nota_percentual}%`",
            cor=discord.Color.green(),
        )

        canal_recrutamentos = guild.get_channel(CANAIS["RECRUTAMENTOS"])
        if canal_recrutamentos:
            await canal_recrutamentos.send(view=NovoRecrutamento(
                candidato=candidato, recrutador=self.aprovador,
                cargo_role=cargo_final, id_fivem=recrutamento.id_fivem, guild=guild,
            ))
        async def excluir_mensagem(mensagem, delay=60):
            """Aguarda o tempo especificado e exclui a mensagem"""
            await asyncio.sleep(delay)
            try:
                await mensagem.delete()
            except discord.NotFound:
                pass  # Mensagem já foi excluída
            except discord.Forbidden:
                print("Sem permissão para excluir a mensagem")


        mensagem = await interaction.followup.send(
                f"✅ **Aprovado como {cargo_final.mention}** por {self.aprovador.mention}",
                ephemeral=True,
            )

        # Agenda a exclusão em 1 minuto (60 segundos)
        asyncio.create_task(excluir_mensagem(mensagem, 60))

        
 