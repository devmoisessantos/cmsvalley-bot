import logging
import traceback

import discord
from discord.ext import commands

from src.bau.bau_panel import PainelBauLayout
from src.bau.bau_setup import garantir_painel_bau
from src.config import (
    CANAIS,
    DISCORD_TOKEN,
    GUILD_ID,
)
from src.database.connection import init_db
from src.database.seed_perguntas import seed_perguntas_se_vazio
from src.gate.gate_panel import PainelEventosGate
from src.gate.gate_presenca import montar_container_presenca
from src.gate.gate_service import listar_eventos_abertos
from src.guia.boas_vindas_panel import PainelBoasVindasLayout
from src.guia.guia_setup import (
    garantir_painel_boas_vindas,
    garantir_painel_tutoriais,
)
from src.guia.tutoriais_panel import PainelTutoriaisLayout
from src.laudos.laudos_panel import PainelLaudosLayout
from src.laudos.laudos_setup import garantir_painel_laudos
from src.membros.cargos_panel import PainelGerenciarCargoLayout
from src.membros.membros_panel import PainelGerenciarMembrosLayout
from src.membros.membros_setup import (
    garantir_painel_gerenciar_cargos,
    garantir_painel_gerenciar_membros,
)
from src.panels.avaliacao_panel import PainelAvaliacaoLayout
from src.panels.setup_paineis import (
    garantir_painel_avaliacao,
    garantir_painel_eventos_gate,
    garantir_painel_fazer_chamada,
    garantir_painel_plantao,
    garantir_painel_recrutamento,
    garantir_painel_whitelist,
)
from src.plantao.chamada.painel_chamada_persistente import PainelFazerChamadaLayout
from src.plantao.plantao_panel import PainelPlantaoLayout
from src.plantao.plantao_tasks import executar_housekeeping_plantao
from src.punicoes.cogs import garantir_painel_punicoes
from src.punicoes.panel import PainelPunicoesLayout
from src.recrutamento.recrutamento_panel import PainelRecrutamentoLayout
from src.utils.deploy_logger import (
    erro,
    etapa,
    fim_deploy,
    inicio_deploy,
    separador,
    sucesso,
)
from src.whitelist.whitelist_panel import PainelWhitelistLayout

intents = discord.Intents.default()
intents.members = True  # necessário para ler/restaurar cargos e apelidos de membros
intents.guilds = True
intents.messages = True
intents.message_content = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cmsvalley-bot")


class CmsValleyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):

        inicio_deploy()

        # Listar extensões (cogs)
        cogs = [
            "src.backup.backup_cogs",
            "src.membros.membros_cogs",
            "src.cogs.utilidade",
            "src.cogs.moderacao",
            "src.cogs.busca",
            "src.cogs.manutencao",
            "src.punicoes.cogs",
            "src.gate.gate_cogs",
            "src.guia.guia_cogs",
            "src.whitelist.whitelist_cogs",
            "src.plantao.plantao_cogs",
            "src.plantao.chamada.chamada_cogs",
            "src.plantao.plantao_tasks",
            "src.plantao.plantao_listener",
            "src.plantao.ranking_plantao_tasks",
            "src.hierarquia.hierarquia",
            "src.hierarquia.hierarquia_class",
            "src.recrutamento.recrutamento_cogs",
            "src.recrutamento.ranking_tasks",
            "src.notificacoes.notificar_cogs",
            "src.templates.templates_cogs",
            "src.laudos.laudos_cogs",
            "src.laudos.ranking_laudos_tasks",
            "src.bau.bau_cogs",
            "src.bau.bau_tasks",
            "src.bau.bau_listener",
            "src.financas.financas_cogs",
        ]

        total = len(cogs)
        for i, cog in enumerate(cogs, 1):
            etapa(i, total, f"Carregando {cog}")
            try:
                await self.load_extension(cog)
                sucesso(f"  {cog} carregado")
            except Exception as e:
                erro(f"  {cog} FALHOU: {e}")

        separador()

        logging.info("Sincronizando comandos de barra...")
        guild_object = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_object)
        await self.tree.sync(guild=guild_object)
        sucesso(f"Comandos sincronizados (ID: {GUILD_ID})")

        logging.info("Conectando ao banco de dados...")
        await init_db()
        await seed_perguntas_se_vazio()
        sucesso("Banco de dados pronto")

        # Inicializa como None - serão criados no on_ready
        self.painel_recrutamento_view = None
        self.painel_avaliacao_view = None
        self.painel_whitelist_view = None
        self.painel_gerenciar_cargos_view = None
        self.painel_eventos_gate_view = None
        self.painel_boas_vindas_view = None
        self.painel_tutoriais_view = None
        self.painel_fazer_chamada_view = None
        self.painel_gerenciar_membros_view = None
        self.painel_punicoes_view = None

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: discord.app_commands.AppCommandError,
        ):
            guild = interaction.guild
            canal = guild.get_channel(CANAIS["LOG_ERROS"]) if guild else None
            tb = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )[-1200:]

            if canal:
                await canal.send(
                    f"⚠️ **Erro em Slash Command**\n"
                    f"Comando: `/{interaction.command.name if interaction.command else '?'}`\n"
                    f"Usuário: {interaction.user.mention}\n"
                    f"```py\n{tb}\n```"
                )

    async def on_ready(self):
        guild = self.get_guild(int(GUILD_ID))
        if guild is None:
            logger.warning("Servidor ainda não encontrado.")
            return

        logger.info(f"✅ Bot conectado como {self.user} (ID: {self.user.id})")

        # no on_ready, uma vez (idempotente — se rodar de novo, não encontra mais nada pra limpar):
        await executar_housekeeping_plantao(self)

        # ═══════════════════════════════════════════════════════════════
        # CRIA as views (só na primeira vez que o bot liga)
        # ═══════════════════════════════════════════════════════════════
        if self.painel_recrutamento_view is None:
            self.painel_recrutamento_view = PainelRecrutamentoLayout(guild=guild)
            self.painel_avaliacao_view = PainelAvaliacaoLayout(guild=guild)
            self.painel_whitelist_view = PainelWhitelistLayout(guild)
            self.painel_gerenciar_cargos_view = PainelGerenciarCargoLayout(guild=guild)
            self.painel_plantao_view = PainelPlantaoLayout(guild)
            self.painel_eventos_gate_view = PainelEventosGate(guild=guild)
            self.painel_boas_vindas_view = PainelBoasVindasLayout(guild)
            self.painel_tutoriais_view = PainelTutoriaisLayout(guild)
            self.painel_fazer_chamada_view = PainelFazerChamadaLayout(guild=guild)
            self.painel_gerenciar_membros_view = PainelGerenciarMembrosLayout(
                guild=guild
            )
            self.painel_punicoes_view = PainelPunicoesLayout(guild=guild)
            self.painel_laudos_view = PainelLaudosLayout(guild)
            self.painel_bau_view = PainelBauLayout(guild)

        # ═══════════════════════════════════════════════════════════════
        # REGISTRA as views persistentes (SEMPRE, em todo reinício)
        # O add_view precisa rodar toda vez que o bot liga, não só na
        # primeira. Se não registrar, os botões das mensagens antigas
        # não são reconhecidos e os painéis "morrem".
        # ═══════════════════════════════════════════════════════════════
        self.add_view(self.painel_recrutamento_view)
        self.add_view(self.painel_avaliacao_view)
        self.add_view(self.painel_whitelist_view)
        self.add_view(self.painel_gerenciar_cargos_view)
        self.add_view(self.painel_plantao_view)
        self.add_view(self.painel_laudos_view)
        self.add_view(self.painel_bau_view)
        self.add_view(self.painel_eventos_gate_view)
        self.add_view(self.painel_boas_vindas_view)
        self.add_view(self.painel_tutoriais_view)
        self.add_view(self.painel_fazer_chamada_view)
        self.add_view(self.painel_gerenciar_membros_view)
        self.add_view(self.painel_punicoes_view)

        # painéis de presença têm custom_id dinâmico por evento — sempre
        # precisa reconstruir e re-registrar, um por evento aberto
        eventos_abertos = await listar_eventos_abertos()
        for evento in eventos_abertos:
            if evento.message_id:
                view_presenca = discord.ui.LayoutView(timeout=None)
                container = await montar_container_presenca(self, evento)
                view_presenca.add_item(container)
                self.add_view(view_presenca, message_id=evento.message_id)

        # Garante que as mensagens existem nos canais
        await garantir_painel_recrutamento(self)
        await garantir_painel_avaliacao(self)
        await garantir_painel_whitelist(self)
        await garantir_painel_gerenciar_cargos(self)
        await garantir_painel_plantao(self)
        await garantir_painel_eventos_gate(self)
        await garantir_painel_boas_vindas(self)
        await garantir_painel_tutoriais(self)
        await garantir_painel_fazer_chamada(self)
        await garantir_painel_gerenciar_membros(self)
        await garantir_painel_punicoes(self)
        await garantir_painel_laudos(self)
        await garantir_painel_bau(self)

        fim_deploy()


bot = CmsValleyBot()


def run():
    bot.run(DISCORD_TOKEN)
