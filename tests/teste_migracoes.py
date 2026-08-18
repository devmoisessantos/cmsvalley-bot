"""Protege migrações contra ordem inválida e comandos destrutivos em produção."""

from src.database.migracoes import MIGRACOES


def teste_migracoes_tem_numeros_unicos_e_sequenciais_a_partir_de_um():
    numeros_das_migracoes = [migracao.numero for migracao in MIGRACOES]
    numeros_esperados = list(range(1, len(MIGRACOES) + 1))

    assert len(numeros_das_migracoes) == len(set(numeros_das_migracoes)), (
        "Cada migração precisa ter um número exclusivo para rodar uma única vez."
    )
    assert numeros_das_migracoes == numeros_esperados, (
        "As migrações precisam ser sequenciais e começar pelo número um."
    )


def teste_toda_migracao_tem_descricao_preenchida_para_auditoria_humana():
    for migracao in MIGRACOES:
        assert migracao.descricao.strip(), (
            f"A migração {migracao.numero} precisa explicar sua finalidade."
        )


def teste_toda_migracao_usa_apenas_alter_ou_create_e_nunca_apaga_dados():
    comandos_proibidos = ("DROP", "DELETE")
    comandos_permitidos = ("ALTER", "CREATE")

    for migracao in MIGRACOES:
        comando_normalizado = migracao.comando_sql.strip().upper()

        assert comando_normalizado.startswith(comandos_permitidos), (
            f"A migração {migracao.numero} deve alterar ou criar estrutura."
        )
        assert not any(
            comando_proibido in comando_normalizado
            for comando_proibido in comandos_proibidos
        ), f"A migração {migracao.numero} não pode apagar estrutura ou dados."
