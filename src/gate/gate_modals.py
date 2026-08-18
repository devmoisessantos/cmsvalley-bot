# src/gate/gate_modals.py
"""
Modais do GATE: criação de eventos e confirmação de presença (ID FiveM).
"""

from __future__ import annotations

import logging

import discord

from src.utils.error_handling import LoggingModalMixin
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)

registrador = logging.getLogger(__name__)


class ModalEventoBase(LoggingModalMixin, discord.ui.Modal):
    """Base dos modais de Treino e Dominas. FacXFac herda e adiciona adversário."""

    dia = discord.ui.TextInput(
        label="Dia",
        placeholder="15/06/2026",
        max_length=20,
    )
    horario = discord.ui.TextInput(
        label="Horário",
        placeholder="20:00",
        max_length=20,
    )
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

    async def on_submit(self, interacao: discord.Interaction):
        """Valida e cria o evento antes de publicar seus painéis auxiliares.

        Confere data, horário, limite e adversário quando aplicável, depois grava o
        evento pelo serviço. Só após confirmar a criação tenta enviar os cards de
        presença e log, evitando painéis sem evento correspondente no banco.
        """
        from src.gate.gate_logger import enviar_log_evento
        from src.gate.gate_presenca_service import enviar_painel_presenca
        from src.gate.gate_service import (
            criar_evento,
            validar_adversario,
            validar_data,
            validar_horario,
            validar_limite,
        )

        data_valida, mensagem_erro = validar_data(self.dia.value)
        if not data_valida:
            await responder_erro(
                interacao,
                titulo="Data inválida",
                linhas=[mensagem_erro],
            )
            return

        horario_valido, mensagem_erro = validar_horario(self.horario.value)
        if not horario_valido:
            await responder_erro(
                interacao,
                titulo="Horário inválido",
                linhas=[mensagem_erro],
            )
            return

        limite_valido, mensagem_erro, limite_inteiro = validar_limite(self.limite.value)
        if not limite_valido:
            await responder_erro(
                interacao,
                titulo="Limite inválido",
                linhas=[mensagem_erro],
            )
            return

        valor_adversario = None
        if hasattr(self, "adversario"):
            valor_adversario = self.adversario.value
            adversario_valido, mensagem_erro = validar_adversario(valor_adversario)
            if not adversario_valido:
                await responder_erro(
                    interacao,
                    titulo="Adversário inválido",
                    linhas=[mensagem_erro],
                )
                return

        evento = await criar_evento(
            tipo=self.tipo,
            titulo=self.title,
            data_evento=self.dia.value.strip(),
            horario=self.horario.value.strip(),
            limite_participantes=limite_inteiro,
            adversario=valor_adversario,
            criado_por=interacao.user.id,
            responsavel_id=interacao.user.id,
        )

        await responder_sucesso(
            interacao,
            titulo="Novo evento criado",
            linhas=[
                f"Evento **{self.title}** criado para "
                f"{self.dia.value} às {self.horario.value}.",
            ],
        )

        painel_ok = await enviar_painel_presenca(interacao.client, evento)
        log_ok = await enviar_log_evento(interacao.client, evento, interacao.guild)

        if not painel_ok or not log_ok:
            # Já respondeu sucesso da criação; só avisa no console
            registrador.info(
                "[GATE] Evento criado, mas algum canal "
                "(presença/log) não foi encontrado."
            )


class ModalFacXFac(ModalEventoBase):
    adversario = discord.ui.TextInput(
        label="Adversário",
        placeholder="Nome da facção",
        max_length=80,
    )

    def __init__(self):
        super().__init__(tipo="facxfac", titulo="FacXFac")


class ModalTreino(ModalEventoBase):
    def __init__(self):
        super().__init__(tipo="treino", titulo="Treino")


class ModalDominas(ModalEventoBase):
    def __init__(self):
        super().__init__(tipo="dominas", titulo="Dominas")


class ModalConfirmarPresenca(discord.ui.Modal, title="Confirmar Presença"):
    """Pede o ID FiveM e registra a presença no evento."""

    id_fivem = discord.ui.TextInput(
        label="Seu ID FiveM",
        placeholder="1186",
        max_length=10,
    )

    def __init__(self, evento_id: int):
        super().__init__()
        self.evento_id = evento_id

    async def on_submit(self, interacao: discord.Interaction):
        """Registra a presença se o FiveM e a elegibilidade do membro forem válidos.

        Converte o campo em número antes de chamar o serviço, que também verifica
        limite e duplicidade. Após o sucesso, atualiza o painel público para que a
        contagem apresentada não fique defasada em relação ao banco.
        """
        from src.gate.gate_presenca_service import atualizar_painel_presenca
        from src.gate.gate_service import (
            confirmar_presenca,
            membro_pertence_a_gate,
        )

        try:
            id_fivem_inteiro = int(self.id_fivem.value.strip())
        except ValueError:
            await responder_erro(
                interacao,
                titulo="ID FiveM inválido",
                linhas=["Informe apenas números no ID FiveM."],
            )
            return

        resultado = await confirmar_presenca(
            self.evento_id,
            interacao.user.id,
            id_fivem_inteiro,
            membro_e_da_gate=membro_pertence_a_gate(interacao.user),
        )

        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Não foi possível confirmar",
                linhas=[resultado.mensagem],
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Presença confirmada",
            linhas=[resultado.mensagem],
        )
        await atualizar_painel_presenca(interacao.client, self.evento_id)
