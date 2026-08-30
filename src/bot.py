"""
Ponto central do bot: cria o cliente, carrega os cogs e sobe os paineis.

Ordem do que acontece quando o bot liga:

1. `setup_hook` roda antes de o bot ficar online. Ele carrega os cogs,
   sincroniza os comandos de barra e prepara o banco de dados.
2. `on_ready` roda depois de o bot estar conectado. Ele cria as views dos
   paineis, registra essas views (isso precisa acontecer em TODO reinicio) e
   garante que a mensagem de cada painel existe no canal certo.
"""

import logging
import os

import discord
from discord.ext import commands

from src.ausencia.ausencia_panel import PainelAusenciaLayout
from src.ausencia.ausencia_setup import garantir_painel_ausencia
from src.avaliacao.avaliacao_panel import PainelAvaliacaoLayout
from src.bau.bau_panel import PainelBauLayout
from src.bau.bau_setup import garantir_painel_bau
from src.config import (
    CANAIS,
    DISCORD_TOKEN,
    GUILD_ID,
)
from src.cursos.cursos_setup import garantir_painel_cursos
from src.database.conexao import init_db
from src.database.seed_perguntas import seed_perguntas_se_vazio
from src.demissao.demissao_setup import garantir_painel_demissao
from src.gate.gate_panel import PainelEventosGate
from src.gate.gate_presenca_service import montar_container_presenca
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
from src.notificacoes.notificacoes_panel import PainelNotificacaoLayout
from src.notificacoes.notificacoes_setup import garantir_painel_notificacao
from src.plantao.chamada.chamada_persistente_panel import PainelFazerChamadaLayout
from src.plantao.plantao_panel import PainelPlantaoLayout
from src.plantao.plantao_tasks import executar_housekeeping_plantao
from src.promocoes.promocoes_setup import garantir_painel_promocao
from src.punicoes.punicoes_cogs import garantir_painel_punicoes
from src.punicoes.punicoes_panel import PainelPunicoesLayout
from src.recrutamento.recrutamento_panel import PainelRecrutamentoLayout
from src.tickets.tickets_panel import (
    PainelTicketDenunciasLayout,
    PainelTicketSuporteLayout,
)
from src.tickets.tickets_setup import (
    garantir_painel_ticket_denuncias,
    garantir_painel_ticket_suporte,
)
from src.tickets.tickets_views import CardBotoesStaffView
from src.utils.deploy_logger import (
    erro,
    etapa,
    fim_deploy,
    inicio_deploy,
    separador,
    sucesso,
)
from src.utils.error_handling import enviar_erro_para_log_erros
from src.utils.log_container import LogContainerView
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
)
from src.utils.setup_paineis import (
    garantir_painel_avaliacao,
    garantir_painel_eventos_gate,
    garantir_painel_fazer_chamada,
    garantir_painel_plantao,
    garantir_painel_recrutamento,
    garantir_painel_whitelist,
)
from src.whitelist.whitelist_panel import PainelWhitelistLayout

# ---------------------------------------------------------------------------
# Permissoes que o bot pede ao Discord
# ---------------------------------------------------------------------------

permissoes_do_bot = discord.Intents.default()
# necessário para ler/restaurar cargos e apelidos de membros
permissoes_do_bot.members = True
permissoes_do_bot.guilds = True
permissoes_do_bot.messages = True
permissoes_do_bot.message_content = True

# ---------------------------------------------------------------------------
# Configuracao do log do projeto
#
# Todo modulo cria o seu proprio registrador com
#     registrador = logging.getLogger(__name__)
# e a configuracao abaixo vale para todos eles de uma vez.
#
# O formato mostra, em cada linha: a hora, o nivel (INFO/WARNING/ERROR),
# de qual modulo a linha veio e a mensagem. Sem o nome do modulo era
# impossivel saber quem tinha escrito no console.
#
# O nivel pode ser trocado sem mexer no codigo, pela variavel de ambiente
# LOG_LEVEL (por exemplo LOG_LEVEL=DEBUG para investigar um problema).
# ---------------------------------------------------------------------------

NIVEL_DE_LOG_ESCOLHIDO = os.getenv("LOG_LEVEL", "INFO").strip().upper()

NIVEIS_DE_LOG_ACEITOS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

nivel_de_log = NIVEIS_DE_LOG_ACEITOS.get(NIVEL_DE_LOG_ESCOLHIDO, logging.INFO)

logging.basicConfig(
    level=nivel_de_log,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)

registrador = logging.getLogger("cmsvalley-bot")

if NIVEL_DE_LOG_ESCOLHIDO not in NIVEIS_DE_LOG_ACEITOS:
    registrador.warning(
        "LOG_LEVEL=%r nao e um nivel conhecido. Usando INFO.",
        NIVEL_DE_LOG_ESCOLHIDO,
    )

# ---------------------------------------------------------------------------
# Lista de cogs (modulos de comandos) que o bot carrega ao ligar
#
# Se um dominio novo for criado, o cog dele precisa ser adicionado aqui,
# senao o sistema simplesmente nao existe quando o bot sobe.
# ---------------------------------------------------------------------------

CAMINHOS_DOS_COGS = [
    "src.backup.backup_cogs",
    "src.backup.recuperacao_cogs",
    "src.membros.membros_cogs",
    "src.utilidade.utilidade_cogs",
    "src.punicoes.moderacao_cogs",
    "src.membros.busca_cogs",
    "src.manutencao.manutencao_cogs",
    "src.avaliacao.avaliacao_cogs",
    "src.punicoes.punicoes_cogs",
    "src.gate.gate_cogs",
    "src.guia.guia_cogs",
    "src.whitelist.whitelist_cogs",
    "src.plantao.plantao_cogs",
    "src.plantao.chamada.chamada_cogs",
    "src.plantao.plantao_tasks",
    "src.plantao.plantao_listener",
    "src.plantao.ranking_plantao_tasks",
    "src.ranking.ranking_cogs",
    "src.hierarquia.hierarquia_cogs",
    "src.hierarquia.hierarquia_class",
    "src.eventos.eventos_cogs",
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
    "src.cursos.cursos_cogs",
    "src.promocoes.promocoes_cogs",
    "src.demissao.demissao_cogs",
    "src.ausencia.ausencia_cogs",
    "src.tickets.tickets_cogs",
    "src.entrada.entrada_listener",
    "src.wipe.wipe_cogs",
    "src.wipe.wipe_listener",
    "src.banco.banco_cogs",
]


class CmsValleyBot(commands.Bot):
    """
    Cliente do bot do CMS Valley.

    Guarda as views persistentes dos paineis como atributos para que elas sejam
    criadas uma unica vez, mas registradas em todo reinicio do bot.
    """

    def __init__(self):
        super().__init__(command_prefix="!", intents=permissoes_do_bot)

        # Guarda quais cogs falharam ao carregar, para avisar no Discord
        # depois que o bot estiver online (no setup_hook ainda nao ha canal).
        self.cogs_que_falharam: list[tuple[str, Exception]] = []

        # Todas as views persistentes comecam vazias e sao preenchidas no
        # on_ready. Ficam declaradas uma por uma, sem getattr/setattr
        # dinamico, para que qualquer pessoa consiga ler a lista completa.
        self.painel_recrutamento_view = None
        self.painel_avaliacao_view = None
        self.painel_whitelist_view = None
        self.painel_gerenciar_cargos_view = None
        self.painel_plantao_view = None
        self.painel_laudos_view = None
        self.painel_bau_view = None
        self.painel_eventos_gate_view = None
        self.painel_boas_vindas_view = None
        self.painel_tutoriais_view = None
        self.painel_fazer_chamada_view = None
        self.painel_gerenciar_membros_view = None
        self.painel_punicoes_view = None
        self.painel_notificacao_view = None
        self.painel_ticket_suporte_view = None
        self.painel_ticket_denuncias_view = None
        self.botoes_ticket_view = None
        self.painel_ausencia_view = None

    # -----------------------------------------------------------------------
    # Subida do bot
    # -----------------------------------------------------------------------

    async def setup_hook(self):
        """
        Roda uma vez, antes de o bot ficar online.

        Carrega os cogs, sincroniza os comandos de barra e prepara o banco.
        """
        inicio_deploy()

        await self._carregar_todos_os_cogs()
        await self._sincronizar_comandos_de_barra()
        await self._preparar_banco_de_dados()

        self.tree.error(self._ao_falhar_comando_de_barra)

    async def _carregar_todos_os_cogs(self):
        """
        Carrega um por um os cogs listados em CAMINHOS_DOS_COGS.

        Se um cog falhar, o bot continua subindo (para nao derrubar o servidor
        inteiro por causa de um sistema), mas a falha e guardada e depois
        avisada no canal de LOG_ERROS pelo on_ready.
        """
        total_de_cogs = len(CAMINHOS_DOS_COGS)

        for numero_do_cog, caminho_do_cog in enumerate(CAMINHOS_DOS_COGS, 1):
            etapa(numero_do_cog, total_de_cogs, f"Carregando {caminho_do_cog}")
            try:
                await self.load_extension(caminho_do_cog)
                sucesso(f"  {caminho_do_cog} carregado")
            except Exception as falha_ao_carregar:
                erro(f"  {caminho_do_cog} FALHOU: {falha_ao_carregar}")
                registrador.exception("Falha ao carregar o cog %s", caminho_do_cog)
                self.cogs_que_falharam.append((caminho_do_cog, falha_ao_carregar))

        separador()

    async def _sincronizar_comandos_de_barra(self):
        """
        Copia os comandos globais para o servidor principal e sincroniza.

        Se o Discord recusar o payload (nome inválido, limite, etc.), o erro
        é logado com a lista de comandos da árvore para facilitar o diagnóstico.
        """
        registrador.info("Sincronizando comandos de barra...")

        servidor_principal = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=servidor_principal)

        try:
            comandos_sincronizados = await self.tree.sync(guild=servidor_principal)
        except Exception as falha_na_sincronizacao:
            nomes_na_arvore = sorted(
                comando.name for comando in self.tree.get_commands()
            )
            erro(
                f"Falha ao sincronizar comandos na guilda {GUILD_ID}: "
                f"{falha_na_sincronizacao}"
            )
            registrador.exception(
                "Falha no tree.sync. Comandos na árvore (%s): %s",
                len(nomes_na_arvore),
                ", ".join(nomes_na_arvore),
            )
            raise

        nomes = sorted(comando.name for comando in comandos_sincronizados)
        sucesso(
            f"Comandos sincronizados (ID: {GUILD_ID}) — "
            f"{len(comandos_sincronizados)} comando(s)"
        )
        registrador.info("Comandos na guilda: %s", ", ".join(nomes))

    async def _preparar_banco_de_dados(self):
        """Cria as tabelas que faltam e semeia as perguntas da prova."""
        registrador.info("Conectando ao banco de dados...")

        await init_db()
        await seed_perguntas_se_vazio()

        sucesso("Banco de dados pronto")

    # -----------------------------------------------------------------------
    # Tratamento de erro dos comandos de barra
    # -----------------------------------------------------------------------

    async def _ao_falhar_comando_de_barra(
        self,
        interacao: discord.Interaction,
        erro_do_comando: discord.app_commands.AppCommandError,
    ):
        """
        Recebe qualquer erro que escapou de um comando de barra.

        Reaproveita `enviar_erro_para_log_erros`, que ja monta o traceback e
        ja tem protecao contra falha no proprio envio do log. Antes, este
        handler mandava texto puro direto no canal e, se esse envio falhasse,
        o erro original se perdia sem deixar rastro.
        """
        nome_do_comando = "?"
        if interacao.command is not None:
            nome_do_comando = interacao.command.name

        try:
            await enviar_erro_para_log_erros(
                interacao.guild,
                titulo="Erro em Slash Command",
                erro=erro_do_comando,
                contexto=f"/{nome_do_comando}",
                usuario=interacao.user,
            )
        except Exception:
            # Se nem o log conseguiu ser enviado, ao menos o console registra.
            registrador.exception(
                "Falha ao registrar no Discord o erro do comando /%s",
                nome_do_comando,
            )

    # -----------------------------------------------------------------------
    # Bot online
    # -----------------------------------------------------------------------

    async def on_ready(self):
        """
        Roda sempre que o bot termina de conectar.

        Cria as views (so na primeira vez), registra todas elas (em todo
        reinicio) e garante que as mensagens dos paineis existem nos canais.
        """
        servidor_principal = self.get_guild(int(GUILD_ID))
        if servidor_principal is None:
            registrador.warning("Servidor ainda não encontrado.")
            return

        registrador.info("✅ Bot conectado como %s (ID: %s)", self.user, self.user.id)

        await self._avisar_sobre_cogs_que_falharam(servidor_principal)

        # Roda uma vez e é idempotente: se rodar de novo, não encontra mais
        # nada para limpar.
        await executar_housekeeping_plantao(self)

        self._criar_views_dos_paineis(servidor_principal)
        self._registrar_views_dos_paineis()
        await self._registrar_views_de_presenca_do_gate()
        await self._garantir_mensagens_dos_paineis()

        fim_deploy()

    async def _avisar_sobre_cogs_que_falharam(self, servidor: discord.Guild):
        """
        Avisa no canal de LOG_ERROS quais cogs nao subiram.

        Sem este aviso, um sistema inteiro podia estar fora do ar e ninguem no
        Discord ficaria sabendo: a falha aparecia apenas no console.
        """
        if not self.cogs_que_falharam:
            return

        canal_de_log_de_erros = servidor.get_channel(CANAIS.get("LOG_ERROS") or 0)
        if canal_de_log_de_erros is None:
            registrador.warning(
                "Canal LOG_ERROS não encontrado: não foi possível avisar sobre "
                "os %d cogs que falharam.",
                len(self.cogs_que_falharam),
            )
            return

        linhas_do_aviso = [
            "Alguns sistemas **não subiram** neste reinício e estão fora do ar:",
            "",
        ]
        for caminho_do_cog, falha_ao_carregar in self.cogs_que_falharam:
            tipo_da_falha = type(falha_ao_carregar).__name__
            linhas_do_aviso.append(
                f"- `{caminho_do_cog}` — {tipo_da_falha}: {falha_ao_carregar}"
            )

        view_do_aviso = LogContainerView(
            titulo="Cogs que falharam ao carregar",
            linhas="\n".join(linhas_do_aviso),
            guild=servidor,
            cor=COR_ERRO if len(self.cogs_que_falharam) > 1 else COR_AVISO,
        )

        try:
            await canal_de_log_de_erros.send(view=view_do_aviso)
        except discord.HTTPException as falha_do_discord:
            registrador.warning(
                "Não foi possível enviar o aviso de cogs que falharam: %s",
                falha_do_discord,
            )

    def _criar_views_dos_paineis(self, servidor: discord.Guild):
        """
        Cria as views persistentes, somente na primeira vez que o bot liga.

        A checagem usa o painel de recrutamento como sentinela: se ele ainda
        esta vazio, nenhuma view foi criada ainda.
        """
        if self.painel_recrutamento_view is not None:
            return

        self.painel_recrutamento_view = PainelRecrutamentoLayout(guild=servidor)
        self.painel_avaliacao_view = PainelAvaliacaoLayout(guild=servidor)
        self.painel_whitelist_view = PainelWhitelistLayout(servidor)
        self.painel_gerenciar_cargos_view = PainelGerenciarCargoLayout(guild=servidor)
        self.painel_plantao_view = PainelPlantaoLayout(servidor)
        self.painel_eventos_gate_view = PainelEventosGate(guild=servidor)
        self.painel_boas_vindas_view = PainelBoasVindasLayout(servidor)
        self.painel_tutoriais_view = PainelTutoriaisLayout(servidor)
        self.painel_fazer_chamada_view = PainelFazerChamadaLayout(guild=servidor)
        self.painel_gerenciar_membros_view = PainelGerenciarMembrosLayout(
            guild=servidor
        )
        self.painel_punicoes_view = PainelPunicoesLayout(guild=servidor)
        self.painel_laudos_view = PainelLaudosLayout(servidor)
        self.painel_bau_view = PainelBauLayout(servidor)
        self.painel_notificacao_view = PainelNotificacaoLayout(guilda=servidor)
        self.painel_ticket_suporte_view = PainelTicketSuporteLayout(guilda=servidor)
        self.painel_ticket_denuncias_view = PainelTicketDenunciasLayout(guilda=servidor)
        self.botoes_ticket_view = CardBotoesStaffView()
        self.painel_ausencia_view = PainelAusenciaLayout(guilda=servidor)

    def _registrar_views_dos_paineis(self):
        """
        Registra as views persistentes no bot. SEMPRE, em todo reinicio.

        O add_view precisa rodar toda vez que o bot liga, não só na primeira.
        Se não registrar, os botões das mensagens antigas não são reconhecidos
        e os painéis "morrem".
        """
        views_para_registrar = [
            ("painel_recrutamento", self.painel_recrutamento_view),
            ("painel_avaliacao", self.painel_avaliacao_view),
            ("painel_whitelist", self.painel_whitelist_view),
            ("painel_gerenciar_cargos", self.painel_gerenciar_cargos_view),
            ("painel_plantao", self.painel_plantao_view),
            ("painel_laudos", self.painel_laudos_view),
            ("painel_bau", self.painel_bau_view),
            ("painel_eventos_gate", self.painel_eventos_gate_view),
            ("painel_boas_vindas", self.painel_boas_vindas_view),
            ("painel_tutoriais", self.painel_tutoriais_view),
            ("painel_fazer_chamada", self.painel_fazer_chamada_view),
            ("painel_gerenciar_membros", self.painel_gerenciar_membros_view),
            ("painel_punicoes", self.painel_punicoes_view),
            ("painel_notificacao", self.painel_notificacao_view),
            ("painel_ticket_suporte", self.painel_ticket_suporte_view),
            ("painel_ticket_denuncias", self.painel_ticket_denuncias_view),
            ("botoes_ticket", self.botoes_ticket_view),
            ("painel_ausencia", self.painel_ausencia_view),
        ]

        for nome_do_painel, view_do_painel in views_para_registrar:
            if view_do_painel is None:
                registrador.warning(
                    "A view '%s' está vazia e não pôde ser registrada.",
                    nome_do_painel,
                )
                continue

            self.add_view(view_do_painel)

    async def _registrar_views_de_presenca_do_gate(self):
        """
        Recria e registra o painel de presenca de cada evento aberto do Gate.

        Estes paineis têm custom_id dinâmico por evento, então não dá para
        registrar uma view fixa: é preciso montar uma por evento aberto e
        amarrá-la ao id da mensagem correspondente.
        """
        eventos_abertos = await listar_eventos_abertos()

        for evento in eventos_abertos:
            if not evento.message_id:
                continue

            view_da_presenca = discord.ui.LayoutView(timeout=None)
            container_da_presenca = await montar_container_presenca(self, evento)
            view_da_presenca.add_item(container_da_presenca)

            self.add_view(view_da_presenca, message_id=evento.message_id)

    async def _garantir_mensagens_dos_paineis(self):
        """
        Garante que a mensagem de cada painel existe no canal certo.

        Cada funcao `garantir_painel_*` e idempotente: se a mensagem ja existe,
        ela apenas atualiza; se nao existe, cria.
        """
        funcoes_que_garantem_paineis = [
            garantir_painel_recrutamento,
            garantir_painel_avaliacao,
            garantir_painel_whitelist,
            garantir_painel_gerenciar_cargos,
            garantir_painel_plantao,
            garantir_painel_eventos_gate,
            garantir_painel_boas_vindas,
            garantir_painel_tutoriais,
            garantir_painel_fazer_chamada,
            garantir_painel_gerenciar_membros,
            garantir_painel_punicoes,
            garantir_painel_laudos,
            garantir_painel_bau,
            garantir_painel_cursos,
            garantir_painel_promocao,
            garantir_painel_demissao,
            garantir_painel_ausencia,
            garantir_painel_notificacao,
            garantir_painel_ticket_suporte,
            garantir_painel_ticket_denuncias,
        ]

        for funcao_que_garante_painel in funcoes_que_garantem_paineis:
            try:
                await funcao_que_garante_painel(self)
            except Exception:
                # Um painel que falha nao pode impedir os outros de subirem.
                registrador.exception(
                    "Falha ao garantir o painel em %s",
                    funcao_que_garante_painel.__name__,
                )


bot = CmsValleyBot()


def run():
    """Liga o bot. Chamado pelo main.py."""
    bot.run(DISCORD_TOKEN)


def executar_bot():
    """Apelido em portugues de `run`. Prefira este em codigo novo."""
    run()
