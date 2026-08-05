import discord

from src.utils.error_handling import LoggingModalMixin
from src.utils.mensagens import responder_card


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
        # import lazy — os dois módulos (services e modal) dependem um do outro,
        # então nenhum dos dois pode importar o outro no topo do arquivo
        from src.gate.evento_gate_lista import enviar_painel_presenca
        from src.gate.evento_gate_services import (
            criar_evento,
            validar_adversario,
            validar_data,
            validar_horario,
            validar_limite,
        )
        from src.gate.gate_logs import enviar_log_evento

        # 👇 Validações em ordem — para na primeira que falhar, avisa o motivo específico
        valido, erro = validar_data(self.dia.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        valido, erro = validar_horario(self.horario.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        valido, erro, limite_int = validar_limite(self.limite.value)
        if not valido:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        # Campo extra só existe no ModalFacXFac — valida só quando presente
        adversario_valor = getattr(self, "adversario", None) and self.adversario.value
        if hasattr(self, "adversario"):
            valido, erro = validar_adversario(adversario_valor)
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
            interaction,
            "Novo Evento Criado",
            [
                f"✅ Evento **{self.title}** criado para {self.dia.value} às {self.horario.value}."
            ],
            delay=10,
            cor=discord.Color.green(),
        )

        # publica o painel de presença no canal correspondente
        await enviar_painel_presenca(interaction.client, evento)
        await enviar_log_evento(interaction.client, evento, interaction.guild)


# ✅ CORRIGIDO: Apenas herda de ModalEventoBase
class ModalFacXFac(ModalEventoBase):
    adversario = discord.ui.TextInput(
        label="Adversário", placeholder="Nome da facção", max_length=80
    )

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
