# src/gate/evento_gate_panel.py

import discord
from datetime import datetime
from asyncio import create_task

from src.gate.list_evento_panel import enviar_painel_presenca
from src.gate.log_gate_panel import enviar_log_evento
from src.utils.mensagens import responder_card
from src.gate.evento_gate_services import criar_evento, encerrar_evento
from src.config import CARGOS_CRIACAO_EVENTO_GATE
from src.utils.error_handling import LoggingViewMixin, LoggingModalMixin

# ---------------------------------------------------------------------------
# VALIDAÇÃO DOS CAMPOS DO MODAL
# ---------------------------------------------------------------------------

def _validar_data(valor: str) -> tuple[bool, str]:
    """Aceita apenas DD/MM/AAAA, com data real (rejeita 32/13/2026, 29/02 em ano não bissexto, etc).
    Retorna (valido, mensagem_de_erro)."""
    valor = valor.strip()
    try:
        data = datetime.strptime(valor, "%d/%m/%Y")
    except ValueError:
        return False, "❌ Data inválida. Use o formato `DD/MM/AAAA` (ex: `15/06/2026`)."

    hoje = datetime.now().date()
    if data.date() < hoje:
        return False, f"❌ A data `{valor}` já passou. Informe uma data futura."

    return True, ""


def _validar_horario(valor: str) -> tuple[bool, str]:
    """Aceita apenas HH:MM em formato 24h (rejeita 25:99, 9h30, etc)."""
    valor = valor.strip()
    try:
        datetime.strptime(valor, "%H:%M")
    except ValueError:
        return False, "❌ Horário inválido. Use o formato `HH:MM`, 24h (ex: `20:00`)."

    return True, ""


def _validar_limite(valor: str) -> tuple[bool, str, int]:
    """0 = sem limite. Retorna (valido, mensagem_de_erro, valor_convertido)."""
    valor = (valor or "0").strip()
    if not valor.isdigit():
        return False, "❌ Limite precisa ser um número inteiro (0 = sem limite).", 0

    limite_int = int(valor)
    if limite_int < 0:
        return False, "❌ Limite não pode ser negativo.", 0
    if limite_int > 25:
        return False, "❌ Limite muito alto (máximo 25). Use `0` para sem limite.", 0

    return True, "", limite_int


def _validar_adversario(valor: str | None) -> tuple[bool, str]:
    """Só chamado pro ModalFacXFac — garante que não veio só espaços em branco."""
    if valor is None or not valor.strip():
        return False, "❌ O nome do adversário não pode ficar em branco."
    return True, ""


# ---------------------------------------------------------------------------
# MODAIS DE CRIAÇÃO
# ---------------------------------------------------------------------------

class ModalEventoBase(LoggingModalMixin, discord.ui.Modal):
    """Base pros modais de Treino/Dominas. FacxFac herda e adiciona 'adversario'."""

    dia = discord.ui.TextInput(label="Dia", placeholder="15/06/2026", max_length=20)
    horario = discord.ui.TextInput(label="Horário", placeholder="20:00", max_length=20)
    limite = discord.ui.TextInput(
        label="Limite de participantes (0 = sem limite)",
        placeholder="0",
        max_length=4,
        required=False,
        default="0",
    )

    def __init__(self, tipo: str, titulo: str):
        super().__init__(title=titulo)
        self.tipo = tipo

    async def on_submit(self, interaction: discord.Interaction):
        # 👇 Validações em ordem — para na primeira que falhar, avisa o motivo específico
        valido, erro = _validar_data(self.dia.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        valido, erro = _validar_horario(self.horario.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        valido, erro, limite_int = _validar_limite(self.limite.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        # Campo extra só existe no ModalFacXFac — valida só quando presente
        adversario_valor = getattr(self, "adversario", None) and self.adversario.value
        if hasattr(self, "adversario"):
            valido, erro = _validar_adversario(adversario_valor)
            if not valido:
                await interaction.response.send_message(erro, ephemeral=True)
                return

        evento = await criar_evento(
            tipo=self.tipo,
            titulo=self.title,
            data_evento=self.dia.value.strip(),
            horario=self.horario.value.strip(),
            limite_participantes=limite_int,
            adversario=adversario_valor,
            criado_por=interaction.user.id,
            responsavel_id=interaction.user.id,
        )

        await responder_card(
            interaction, "Novo Evento Criado",            
            [f"✅ Evento **{self.title}** criado para {self.dia.value} às {self.horario.value}."],
            delay=10,
            cor=discord.Color.green(),
        )

        # publica o painel de presença no canal correspondente
        await enviar_painel_presenca(interaction.client, evento)
        await enviar_log_evento(interaction.client, evento, interaction.guild)

# ✅ CORRIGIDO: Apenas herda de ModalEventoBase
class ModalFacXFac(ModalEventoBase):
    adversario = discord.ui.TextInput(label="Adversário", placeholder="Nome da facção", max_length=80)

    def __init__(self):
        super().__init__(tipo="facxfac", titulo="FacXFac")


# ✅ CORRIGIDO: Apenas herda de ModalEventoBase
class ModalTreino(ModalEventoBase):
    def __init__(self):
        super().__init__(tipo="treino", titulo="Treino")


# ✅ CORRIGIDO: Apenas herda de ModalEventoBase
class ModalDominas(ModalEventoBase):
    def __init__(self):
        super().__init__(tipo="dominas", titulo="Dominas")


# ---------------------------------------------------------------------------
# PAINEL FIXO — EVENTOS GATE
# ---------------------------------------------------------------------------

class PainelEventosGate(LoggingViewMixin, discord.ui.LayoutView):
    """View persistente (timeout=None) renderizada com Container (Components V2)."""
    def __init__(self, guild: discord.Guild = None):
        super().__init__(timeout=None)
        self.guild = guild

        row = discord.ui.ActionRow()
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🛡️ Criar Evento GATE\n"
                "**> Painel dedicato à criação de eventos.**"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                "## Agendamento de eventos",  # ← título
                (
                    "Utilize os botões abaixo para iniciar ou encerrar algum evento.\n"
                    "**Lembre-se:** você deve ser um membro autorizado!\n\n"
                ),  # ← descrição
                accessory=discord.ui.Thumbnail(guild.icon.url) if guild and guild.icon else None,
            ),
        )
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        row.add_item(
            discord.ui.Button(
                label="🚩 Treinamento", 
                style=discord.ButtonStyle.secondary, 
                custom_id="gate:treino"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="☠️ FAC x FAC", 
                style=discord.ButtonStyle.success, 
                custom_id="gate:facxfac"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="⚔️ Dominas", 
                style=discord.ButtonStyle.green, 
                custom_id="gate:dominas"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="❌ Encerrar", 
                style=discord.ButtonStyle.danger, 
                custom_id="gate:encerrar"
            )
        )
        container.add_item(row)
        self.add_item(container)


def _tem_permissao_gate(member: discord.Member) -> bool:
    return any(role.name in CARGOS_CRIACAO_EVENTO_GATE for role in member.roles)


async def registrar_listener_gate(bot: discord.Client):
    @bot.listen("on_interaction")
    async def _on_gate_interaction(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("gate:"):
            return

        if not _tem_permissao_gate(interaction.user):
            await responder_card(
                interaction, 
                "❌ Sem Permissão",
                ["Você não tem permissão para gerenciar eventos do GATE."],
                cor=discord.Color.red(),
            )
            return

        acao = custom_id.split(":", 1)[1]

        if acao == "treino":
            await interaction.response.send_modal(ModalTreino())
        elif acao == "facxfac":
            await interaction.response.send_modal(ModalFacXFac())
        elif acao == "dominas":
            await interaction.response.send_modal(ModalDominas())
        elif acao == "encerrar":
            ok = await encerrar_evento(interaction.user.id)
            msg = "✅ Listagem para evento encerrado." if ok else "Nenhum evento em aberto encontrado."
            await interaction.response.send_message(msg, ephemeral=True)