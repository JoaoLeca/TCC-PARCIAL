# Entrega de atendimento para o TCC

O pedido aprovado é implementar as melhorias de confiabilidade do Telegram, entendimento da conversa, alternativas de disponibilidade, atendimento humano, avaliação e usabilidade do agendamento. O calendário e os feriados já ajustados devem ser preservados.

## Decisões de implementação

Usar o site e o Telegram existentes, com as mesmas regras de agenda no Python e SQLite. Consultas e encaminhamento humano devem funcionar mesmo quando a IA estiver indisponível. O GPT pode interpretar e consultar informações atuais, enquanto a reserva continua exigindo seleção e confirmação explícita. Alterações de reservas exigem telefone e código privado.

Contato para retorno será coletado fora do contexto do modelo. A equipe terá uma fila privada com responsável e estados Aberto, Em atendimento e Resolvido. O dono verá todos os chamados, e cada barbeiro poderá assumir um chamado livre e gerenciar os seus. FCR e acerto das intenções dependem de avaliação humana ou rótulos de referência. As métricas operacionais serão calculadas a partir de eventos reais, sem simular resultados de pesquisa.

## Plano de implementação

- [x] Telegram. Vincular botões à vaga exata e à sessão do fluxo, recuperar envio de comprovante, impedir duplicação por repetição e remover códigos privados do histórico persistido. Testar botões antigos, vaga ocupada, repetição e falha de envio.
- [x] Atendimento humano. Migrar o banco preservando chamados existentes, criar serviços de contato vinculados à sessão, proteger a fila e as transições de responsável no servidor e disponibilizar a interface privada. Testar autorização, concorrência, migração e ciclo do chamado.
- [x] Conversa. Sanitizar mensagens em ambos os canais, usar contexto nas perguntas curtas, buscar até três opções reais e alternativas por dia/profissional e oferecer recuperação quando a IA falhar. Testar continuidade, período do dia e ausência de vagas.
- [x] Agendamento. Mostrar resumo, explicar dias indisponíveis, preservar contato ao voltar e confirmar novamente a vaga antes da gravação. Usar chave de idempotência para repetir uma confirmação sem duplicar reserva.
- [x] Avaliação. Registrar eventos de conversa e reservas, exportar CSV sem contato ou texto das mensagens, calcular latência/encaminhamentos/reservas e documentar os três cenários de demonstração.
- [x] Sugestões clicáveis do chat abrem a revisão com as escolhas preenchidas, revalidam catálogo e disponibilidade e preservam a confirmação final.
- [x] Preferências numéricas de horário funcionam sem IA, respeitam duração e bloqueios e pedem esclarecimento para ambiguidades e negações.
- [x] O contato humano informa retorno por telefone, com protocolo somente após o envio explícito do formulário no site.
- [x] Integração. Executar a suíte, revisar as alterações, conferir o site desktop/móvel e a API local. As credenciais reais devem ser configuradas localmente pelo responsável, sem publicar segredos.

## Verificações centrais

Uma alteração concorrente na agenda deve resultar em nova escolha, nunca em outro horário reservado silenciosamente. A repetição de uma confirmação deve retornar a mesma reserva. Um chamado só pode ser atualizado pelo cliente na sessão que o criou ou pela equipe autorizada. A falta de chave da IA deve ser explicada sem impedir o fluxo guiado. A avaliação não pode contabilizar uma resposta como resolução sem confirmação dos avaliadores.

## Resultado da validação local

Foram aprovados 188 testes automatizados na validação consolidada. O ensaio isolado no navegador verificou consultas com contexto e alternativas, envio de contato, atendimento assumido e resolvido e confirmação de reserva no celular. A revisão independente não deixou falhas críticas ou importantes pendentes.

Conforme a escolha do usuário, a validação da OpenAI e do Telegram reais fica pendente de configuração de credenciais. Os testes do Telegram usam transporte simulado.
