"""Protege regras de cargos para impedir acessos indevidos aos comandos do bot."""

from src.utils.permissions import (
    membro_e_administrador,
    membro_tem_algum_cargo,
    membro_tem_cargo,
    membro_tem_cargo_por_id,
)


class CargoFalso:
    """Representa somente os atributos de cargo usados pelas regras puras."""

    def __init__(self, nome: str, identificador: int) -> None:
        self.name = nome
        self.id = identificador


class PermissoesDaGuildaFalsas:
    """Representa somente a permissão nativa de administrador do Discord."""

    def __init__(self, e_administrador: bool) -> None:
        self.administrator = e_administrador


class MembroFalso:
    """Dublê legível de membro do Discord usado nas verificações de permissão."""

    def __init__(self, cargos: list[CargoFalso], e_administrador: bool = False) -> None:
        self.roles = cargos
        self.guild_permissions = PermissoesDaGuildaFalsas(e_administrador)


def teste_membro_tem_cargo_reconhece_nome_exato_e_rejeita_nome_ausente():
    membro_com_cargos = MembroFalso(
        [
            CargoFalso("Médico", 101),
            CargoFalso("Admin", 202),
        ]
    )

    assert membro_tem_cargo(membro_com_cargos, "Admin") is True
    assert membro_tem_cargo(membro_com_cargos, "admin") is False
    assert membro_tem_cargo(membro_com_cargos, "Fundador") is False


def teste_membro_tem_algum_cargo_aceita_lista_tupla_e_conjunto_de_nomes_permitidos():
    membro_com_cargo_de_recrutador = MembroFalso([CargoFalso("Recrutador", 303)])

    assert (
        membro_tem_algum_cargo(
            membro_com_cargo_de_recrutador,
            ["Admin", "Recrutador"],
        )
        is True
    )
    assert (
        membro_tem_algum_cargo(
            membro_com_cargo_de_recrutador,
            ("Fundador", "Recrutador"),
        )
        is True
    )
    assert (
        membro_tem_algum_cargo(
            membro_com_cargo_de_recrutador,
            {"Recrutador"},
        )
        is True
    )
    assert membro_tem_algum_cargo(membro_com_cargo_de_recrutador, []) is False


def teste_membro_tem_cargo_por_id_compara_identificador_e_nao_nome_do_cargo():
    membro_com_dois_cargos = MembroFalso(
        [
            CargoFalso("Cargo Renomeado", 404),
            CargoFalso("Outro Cargo", 505),
        ]
    )

    assert membro_tem_cargo_por_id(membro_com_dois_cargos, 404) is True
    assert membro_tem_cargo_por_id(membro_com_dois_cargos, 999) is False


def teste_membro_e_administrador_aceita_permissao_nativa_ou_cargo_configurado():
    membro_com_permissao_nativa = MembroFalso([], e_administrador=True)
    membro_com_cargo_administrativo = MembroFalso([CargoFalso("Admin", 606)])
    membro_sem_permissao = MembroFalso([CargoFalso("Médico", 707)])

    assert membro_e_administrador(membro_com_permissao_nativa) is True
    assert membro_e_administrador(membro_com_cargo_administrativo) is True
    assert membro_e_administrador(membro_sem_permissao) is False
