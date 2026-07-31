import discord
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from src.config import CANAIS, CARGOS
from src.database.connection import async_session
from src.database.models import EstadoPlantao, Recrutamento, Chamada
from src.services.plantao_service import membro_e_doutor_ou_acima, garantir_aware
from src.services.chamada_service import (
    calcular_proximo_horario_permitido, tentar_iniciar_chamada, finalizar_chamada,
    registrar_falta,
)
from src.services.ocr_ems_service import extrair_linhas_do_print_ems
from src.services.chamada_service import extrair_entradas_do_ems  # parser
from src.services.chamada_state import SessaoChamada, MedicoNaChamada, definir_sessao, obter_sessao
from src.utils.log_container import criar_container_log, LogContainerView
from src.utils.error_handling import LoggingViewMixin

logger = logging.getLogger(__name__)


class PainelCoordenacaoView(LoggingViewMixin, discord.ui.LayoutView):
    """Painel exibido quando o Doutor+ ativa o Modo Coordenação."""

    def __init__(self, membro: discord.Member, proximo_horario, liberado: bool):
        super().__init__(timeout=180)
        self.membro = membro

        if liberado:
            status_texto = "🟢 **Chamada liberada agora.**"
        else:
            timestamp = int(proximo_horario.timestamp())
            status_texto = f"🔒 Próxima chamada liberada <t:{timestamp}:R> (<t:{timestamp}:t>)"

        linhas = (
            f"`🩺` **Modo Coordenação ativo** — {membro.mention}\n"
            f"`📋` {status_texto}\n"
            "`ℹ️` Ao realizar a chamada, você vai enviar um print do `/ems` "
            "e o sistema vai cruzar com quem está de plantão."
        )

        componentes = [
            discord.ui.TextDisplay("# 🧭 Painel de Coordenação"),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        row = discord.ui.ActionRow()

        botao_chamada = discord.ui.Button(
            label="🩺 Realizar Chamada",
            style=discord.ButtonStyle.success,
            disabled=not liberado,
        )
        botao_chamada.callback = self._callback_realizar_chamada
        row.add_item(botao_chamada)

        botao_voltar = discord.ui.Button(label="↩️ Sair do Modo Coordenação", style=discord.ButtonStyle.secondary)
        botao_voltar.callback = self._callback_sair
        row.add_item(botao_voltar)

        componentes.append(row)

        self.container = discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
        self.add_item(self.container)

    @classmethod
    async def construir(cls, membro: discord.Member) -> "PainelCoordenacaoView":
        proximo_horario, liberado = await calcular_proximo_horario_permitido()
        return cls(membro, proximo_horario, liberado)

    async def _callback_sair(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == interaction.user.id)
            )
            estado = resultado.scalar_one_or_none()
            if estado:
                estado.modo_coordenacao = False
                await session.commit()

        from src.panels.plantao_panel import InformacoesPlantaoView, _buscar_estado
        novo_estado = await _buscar_estado(interaction.user.id)
        nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
        await interaction.edit_original_response(view=nova_view)

    async def _callback_realizar_chamada(self, interaction: discord.Interaction):
        if not membro_e_doutor_ou_acima(interaction.user):
            await interaction.response.send_message("❌ Apenas Doutor ou acima pode realizar chamadas.", ephemeral=True)
            return

        conseguiu, outro_doutor_id = await tentar_iniciar_chamada(interaction.user.id)
        if not conseguiu:
            await interaction.response.send_message(
                f"⏳ <@{outro_doutor_id}> já está realizando uma chamada agora. Aguarde o próximo período.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            registro_chamada = Chamada(doutor_id=interaction.user.id)
            session.add(registro_chamada)
            await session.commit()
            chamada_id = registro_chamada.id

        sessao = SessaoChamada(
            doutor_id=interaction.user.id,
            chamada_id=chamada_id,
            canal_id=interaction.channel_id,
        )
        definir_sessao(sessao)

        view_aguardando = _construir_view_aguardando_print()
        mensagem = await interaction.followup.send(view=view_aguardando, ephemeral=True)

        bot = interaction.client

        def checagem(msg: discord.Message) -> bool:
            return (
                msg.author.id == interaction.user.id
                and msg.channel.id == interaction.channel_id
                and len(msg.attachments) > 0
            )

        try:
            mensagem_print = await bot.wait_for("message", timeout=300, check=checagem)
        except TimeoutError:
            await finalizar_chamada(marcar_ultima_chamada=False)
            definir_sessao(None)
            await interaction.followup.send(
                "⏱️ Tempo esgotado esperando o print do `/ems`. Chamada cancelada — tente novamente.",
                ephemeral=True,
            )
            return

        anexo = mensagem_print.attachments[0]
        await _processar_print_ems(interaction, anexo.url)


def _construir_view_aguardando_print() -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = criar_container_log(
        titulo="📸 Aguardando Print do /ems",
        linhas=(
            "`•` Envie **neste canal** um print do comando `/ems`, o mais legível possível.\n"
            "`•` O sistema vai identificar os IDs FiveM automaticamente.\n"
            "`•` Você tem 5 minutos."
        ),
        guild=None,  # ajustado abaixo
        cor=discord.Color.gold(),
    ) if False else None
    # criar_container_log exige guild pra rodapé — versão simplificada sem rodapé aqui:
    componentes = [
        discord.ui.TextDisplay("# 📸 Aguardando Print do /ems"),
        discord.ui.TextDisplay(
            "`•` Envie **neste canal** um print do comando `/ems`, o mais legível possível.\n"
            "`•` O sistema vai identificar os IDs FiveM automaticamente.\n"
            "`•` Você tem 5 minutos."
        ),
    ]
    view.add_item(discord.ui.Container(*componentes, accent_color=discord.Color.gold()))
    return view


async def _processar_print_ems(interaction: discord.Interaction, url_imagem: str):
    sessao = obter_sessao()
    guild = interaction.guild

    await interaction.followup.send("🔍 Processando imagem, aguarde...", ephemeral=True)

    linhas_com_confianca = await extrair_linhas_do_print_ems(url_imagem)
    resultado_parser = extrair_entradas_do_ems(linhas_com_confianca)
    todas_entradas = resultado_parser["confiaveis"] + resultado_parser["revisar"]

    sessao.total_medicos_ems = len(todas_entradas)

    ids_no_ems = set()
    reconhecidos_sul: list[MedicoNaChamada] = []
    nao_reconhecidos: list[dict] = []

    async with async_session() as session:
        for entrada in todas_entradas:
            id_fivem = entrada["id_fivem"]
            ids_no_ems.add(id_fivem)

            resultado = await session.execute(
                select(Recrutamento.discord_id_candidato)
                .where(Recrutamento.id_fivem == id_fivem, Recrutamento.status == "APROVADO")
                .order_by(Recrutamento.id.desc())
                .limit(1)
            )
            discord_id = resultado.scalar_one_or_none()

            if discord_id is not None:
                membro_db = guild.get_member(discord_id)
                reconhecidos_sul.append(MedicoNaChamada(
                    id_fivem=id_fivem, discord_id=discord_id,
                    nome_ems=entrada["nome_ems"],
                    nome_discord=membro_db.display_name if membro_db else None,
                    confianca=entrada["confianca"],
                ))
            else:
                nao_reconhecidos.append(entrada)

        # Quem está com toggle ligado no Discord agora
        resultado_toggle = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.toggle_ligado.is_(True))
        )
        estados_ligados = resultado_toggle.scalars().all()

    sessao.total_toggle_ligado = len(estados_ligados)
    ids_fivem_com_toggle = {e.id_fivem: e.discord_id for e in estados_ligados if e.id_fivem}

    # Cruzamento: reconhecidos no EMS E com toggle ligado → vão pra confirmação de presença
    ids_reconhecidos_com_toggle = {m.id_fivem for m in reconhecidos_sul} & set(ids_fivem_com_toggle.keys())
    sessao.presentes_no_ems_toggle_ligado = [
        m for m in reconhecidos_sul if m.id_fivem in ids_reconhecidos_com_toggle
    ]

    # Toggle ligado mas NÃO apareceu em lugar nenhum no print do EMS
    ids_com_toggle_fora_do_ems = set(ids_fivem_com_toggle.keys()) - ids_no_ems
    sessao.toggle_ligado_mas_nao_no_ems = [
        MedicoNaChamada(id_fivem=id_fivem, discord_id=ids_fivem_com_toggle[id_fivem], nome_ems="—")
        for id_fivem in ids_com_toggle_fora_do_ems
    ]

    sessao.nao_reconhecidos = nao_reconhecidos

    # Resolve automaticamente quem está com toggle ligado mas sumiu do EMS
    await _processar_ausentes_do_ems(interaction, sessao)

    await _enviar_view_revisao(interaction, sessao)


async def _processar_ausentes_do_ems(interaction: discord.Interaction, sessao: SessaoChamada):
    """Quem tem toggle ligado no Discord mas não apareceu no print do /ems: avisa,
    desliga o serviço automaticamente e loga — não depende de confirmação manual do Doutor."""
    from src.services.plantao_service import desligar_servico

    guild = interaction.guild
    for medico in sessao.toggle_ligado_mas_nao_no_ems:
        membro = guild.get_member(medico.discord_id)
        if membro is None:
            continue

        try:
            await membro.send(
                "⚠️ Durante a chamada de auditoria, você estava com o plantão ativo no Discord "
                "mas não foi encontrado no `/ems` da cidade. Seu plantão foi encerrado automaticamente. "
                "Lembre-se de manter o toggle da cidade ativo enquanto estiver de plantão."
            )
        except discord.Forbidden:
            pass

        await desligar_servico(membro)

        await registrar_falta(
            membro.id, sessao.chamada_id,
            motivo="Toggle ligado no Discord, ausente no /ems da cidade",
            guild=guild,
        )


async def _enviar_view_revisao(interaction: discord.Interaction, sessao: SessaoChamada):
    linhas = (
        f"`📋` Médicos no `/ems`: **{sessao.total_medicos_ems}**\n"
        f"`🟢` Toggle ligado no Discord: **{sessao.total_toggle_ligado}**\n"
        f"`✅` Reconhecidos (SUL) para conferência: **{len(sessao.presentes_no_ems_toggle_ligado)}**\n"
        f"`❓` Não reconhecidos (Norte/desconhecido): **{len(sessao.nao_reconhecidos)}**\n"
        f"`⚠️` Já processados automaticamente (toggle ligado, ausente do EMS): "
        f"**{len(sessao.toggle_ligado_mas_nao_no_ems)}**"
    )

    view = discord.ui.LayoutView(timeout=300)
    componentes = [
        discord.ui.TextDisplay("# 🔍 Resultado do Processamento"),
        discord.ui.TextDisplay(linhas),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
    ]

    row = discord.ui.ActionRow()

    if sessao.nao_reconhecidos:
        botao_manual = discord.ui.Button(label="➕ Adicionar Manualmente", style=discord.ButtonStyle.secondary)
        botao_manual.callback = _callback_abrir_modal_manual
        row.add_item(botao_manual)

    botao_continuar = discord.ui.Button(label="➡️ Confirmar Presenças", style=discord.ButtonStyle.primary)
    botao_continuar.callback = _callback_ir_para_presenca
    row.add_item(botao_continuar)

    componentes.append(row)
    view.add_item(discord.ui.Container(*componentes, accent_color=discord.Color.blurple()))

    await interaction.followup.send(view=view, ephemeral=True)


class ModalAdicionarManual(discord.ui.Modal, title="Adicionar Médico Manualmente"):
    identificador = discord.ui.TextInput(
        label="ID FiveM, Discord ID ou Menção",
        placeholder="Ex: 054623 ou @membro ou 859100649366356000",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        sessao = obter_sessao()
        if sessao is None:
            await interaction.response.send_message("❌ Sessão de chamada expirada.", ephemeral=True)
            return

        valor = self.identificador.value.strip().replace("<@", "").replace(">", "").replace("!", "")

        discord_id = None
        id_fivem = None

        if valor.isdigit() and len(valor) >= 15:  # provável Discord ID / menção
            discord_id = int(valor)
        elif valor.isdigit():
            id_fivem = valor

        async with async_session() as session:
            if id_fivem:
                resultado = await session.execute(
                    select(Recrutamento.discord_id_candidato)
                    .where(Recrutamento.id_fivem == id_fivem, Recrutamento.status == "APROVADO")
                    .order_by(Recrutamento.id.desc()).limit(1)
                )
                discord_id = resultado.scalar_one_or_none()
            elif discord_id:
                resultado = await session.execute(
                    select(Recrutamento.id_fivem)
                    .where(Recrutamento.discord_id_candidato == discord_id, Recrutamento.status == "APROVADO")
                    .order_by(Recrutamento.id.desc()).limit(1)
                )
                id_fivem = resultado.scalar_one_or_none()

        if discord_id is None or id_fivem is None:
            await interaction.response.send_message(
                "❌ Não foi possível encontrar um Recrutamento aprovado com esse identificador.",
                ephemeral=True,
            )
            return

        membro = interaction.guild.get_member(discord_id)
        sessao.presentes_no_ems_toggle_ligado.append(MedicoNaChamada(
            id_fivem=id_fivem, discord_id=discord_id,
            nome_ems="(adicionado manualmente)",
            nome_discord=membro.display_name if membro else None,
            origem="manual",
        ))
        sessao.nao_reconhecidos = [e for e in sessao.nao_reconhecidos if e["id_fivem"] != id_fivem]

        await interaction.response.send_message(f"✅ Adicionado: <@{discord_id}> (`{id_fivem}`)", ephemeral=True)


async def _callback_abrir_modal_manual(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalAdicionarManual())


async def _callback_ir_para_presenca(interaction: discord.Interaction):
    sessao = obter_sessao()
    if sessao is None or not sessao.presentes_no_ems_toggle_ligado:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("ℹ️ Nenhum médico pra confirmar presença — finalizando chamada.", ephemeral=True)
        await _finalizar_e_logar(interaction, faltantes_ids=set())
        return

    await interaction.response.defer(ephemeral=True)

    opcoes = [
        discord.SelectOption(
            label=f"{m.nome_discord or m.nome_ems} | {m.id_fivem}",
            value=str(m.discord_id),
        )
        for m in sessao.presentes_no_ems_toggle_ligado[:25]  # limite do Discord
    ]

    select = discord.ui.Select(
        placeholder="Marque quem NÃO respondeu (call e/ou rádio) — faltantes",
        options=opcoes, min_values=0, max_values=len(opcoes),
    )

    async def callback_select(inter: discord.Interaction):
        faltantes_ids = {int(v) for v in inter.data["values"]}
        await inter.response.defer(ephemeral=True)
        await _finalizar_e_logar(inter, faltantes_ids)

    select.callback = callback_select

    view = discord.ui.View(timeout=180)
    view.add_item(select)
    await interaction.followup.send(
        "🔎 Confirme no dropdown quem **não respondeu** (rádio ou call) — o resto será marcado como presente.",
        view=view, ephemeral=True,
    )


async def _finalizar_e_logar(interaction: discord.Interaction, faltantes_ids: set[int]):
    sessao = obter_sessao()
    guild = interaction.guild
    presentes_ids = []
    ausentes_ids = []

    for medico in sessao.presentes_no_ems_toggle_ligado:
        membro = guild.get_member(medico.discord_id)
        if membro is None:
            continue

        if medico.discord_id in faltantes_ids:
            ausentes_ids.append(medico.discord_id)
            await registrar_falta(membro.id, sessao.chamada_id, "Não respondeu à chamada (call/rádio)", guild)

            async with async_session() as session:
                resultado = await session.execute(
                    select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
                )
                estado = resultado.scalar_one_or_none()
                tinha_30min_mais = estado is not None and estado.saldo_moedas > 0  # regra simplificada
                await session.commit()

            from src.services.plantao_service import desligar_servico
            await desligar_servico(membro)

            try:
                await membro.send(
                    "🔴 Você foi desconectado e seu plantão encerrado por não responder à chamada de auditoria."
                )
            except discord.Forbidden:
                pass
        else:
            presentes_ids.append(medico.discord_id)
            async with async_session() as session:
                resultado = await session.execute(
                    select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
                )
                estado = resultado.scalar_one_or_none()
                if estado:
                    estado.saldo_moedas += 1
                await session.commit()

    # Recompensa pro Doutor que realizou a chamada
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == sessao.doutor_id)
        )
        estado_doutor = resultado.scalar_one_or_none()
        if estado_doutor:
            estado_doutor.saldo_moedas += 1
            await session.commit()

        resultado_chamada = await session.execute(select(Chamada).where(Chamada.id == sessao.chamada_id))
        registro_chamada = resultado_chamada.scalar_one_or_none()
        if registro_chamada:
            registro_chamada.total_medicos_ems = sessao.total_medicos_ems
            registro_chamada.total_toggle_ligado = sessao.total_toggle_ligado
            registro_chamada.total_presentes = len(presentes_ids)
            registro_chamada.total_ausentes = len(ausentes_ids) + len(sessao.toggle_ligado_mas_nao_no_ems)
            await session.commit()

    canal_log = guild.get_channel(CANAIS.get("LOG_CHAMADAS"))
    if canal_log:
        linhas_log = (
            f"`👨‍⚕️` Realizada por: <@{sessao.doutor_id}>\n"
            f"`📋` Total no `/ems`: **{sessao.total_medicos_ems}**\n"
            f"`🟢` Toggle ligado (Discord): **{sessao.total_toggle_ligado}**\n"
            f"`✅` Presentes: **{len(presentes_ids)}**\n"
            f"`❌` Ausentes (não respondeu): **{len(ausentes_ids)}**\n"
            f"`⚠️` Ausentes (não estavam no EMS): **{len(sessao.toggle_ligado_mas_nao_no_ems)}**"
        )
        view_log = LogContainerView(
            titulo="📋 Chamada de Plantão Realizada",
            linhas=linhas_log, guild=guild, cor=discord.Color.blurple(),
        )
        await canal_log.send(view=view_log)

    await finalizar_chamada(marcar_ultima_chamada=True)
    definir_sessao(None)

    await interaction.followup.send("✅ Chamada finalizada e registrada.", ephemeral=True)