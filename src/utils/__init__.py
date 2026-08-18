"""
Base compartilhada: codigo que todo dominio pode usar.

O modulo mensagens.py e o mais importante daqui: toda resposta ao membro sai
dele. Nada em utils deve depender de um dominio especifico, senao vira import
circular.
"""
