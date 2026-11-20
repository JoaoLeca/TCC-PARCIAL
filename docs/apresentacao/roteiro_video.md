# Roteiro da apresentação oral e do vídeo

Material para a apresentação do TCC e para o vídeo que o representante publica no YouTube.

- **Slides:** [`apresentacao_tcc.pptx`](apresentacao_tcc.pptx), editável no PowerPoint, com a fala sugerida de cada slide nas notas do apresentador. Há também uma cópia em [PDF](apresentacao_tcc.pdf).
- **Duração estimada:** cerca de 22 minutos com a demonstração. Se o tempo máximo for menor, veja [Versão curta](#versão-curta).
- **Divisão das falas:** é uma sugestão e pode ser trocada. Para mudar, edite o nome no início de cada nota.

## Sequência

| Slide | Conteúdo | Quem fala | Tempo |
| --- | --- | --- | --- |
| 1 | Título e apresentação do grupo | Leonardo | 1 min |
| 2 | O problema e a pergunta de pesquisa | Leonardo | 1,5 min |
| 3 | Objetivo | Leonardo | 1 min |
| 4 | Fundamentos: modelos de linguagem, alucinação, RAG e agentes | João | 1,5 min |
| 5 | Arquitetura | João | 1,5 min |
| 6 | A IA não grava reservas | João | 1,5 min |
| 7 | Como o chat funciona: agente, regras de reserva e equipe | Caetano | 1,5 min |
| 8 | O sistema em uso | Caetano | 1 min |
| 9 | Demonstração | Caetano | 3 min |
| 10 | Acesso às reservas e recuperação pelo Telegram | João | 1,5 min |
| 11 | Barbeiros no Telegram: vínculo, avisos e agenda | João | 1 min |
| 12 | Segurança e privacidade | João | 1 min |
| 13 | Testes automatizados | Leonardo | 1,5 min |
| 14 | Ensaios de aceitação e defeito corrigido | Leonardo | 1,5 min |
| 15 | Limitações e próximos passos | Caetano | 1,5 min |
| 16 | Conclusão e perguntas | Leonardo | 1 min |

## Demonstração (slide 9)

Prepare a demonstração antes de gravar, em uma instalação local com **banco separado** e **dados fictícios**. Nunca use o banco real nem dados de clientes.

1. Na pasta do projeto, defina `DATABASE_PATH` para um banco novo (por exemplo, `data/demo.db`) no `.env` local, crie a conta de dono com `uv run python -m barber_bot.staff dono` e inicie a API e o site conforme o README (ou use `IniciarDemoWindows.bat`). Para as etapas 3 e 4, configure também `OPENAI_API_KEY` no `.env` local e gere o índice RAG conforme o README.
2. Abra o site em uma janela anônima, para começar com uma sessão nova.
3. Siga as etapas do slide 9:
   1. Na aba **Agendar**, escolha Corte de Cabelo, um dia e um horário, revise o resumo, informe um nome e um telefone fictícios, como “Cliente Demonstração” e (16) 90000-0000, e confirme.
   2. Abra **Meus horários** e mostre a reserva. Em outra aba, entre em `/equipe` com a conta de dono e mostre a reserva na agenda.
   3. Com a chave configurada, escreva no chat “Quero agendar corte amanhã depois das 15h” e clique em uma das sugestões; mostre que a revisão abre preenchida e só grava após a confirmação.
   4. Com a chave configurada, escreva no chat “quero remarcar minha reserva”, informe um dia à tarde, escolha a opção 1 e responda “sim”. Atualize **Meus horários**.
   5. Clique em **Falar com a equipe**, envie nome e telefone fictícios e mostre o chamado em **Atendimento** na área da equipe.
4. Se algo falhar durante a gravação, siga com o slide 9 e explique a captura da remarcação que aparece nele.

Sem a chave da OpenAI, o chat responde apenas que o atendimento virtual não está habilitado. Nesse caso, mostre esse aviso, explique que o agendamento continua pelas abas do site e pelos menus do Telegram e faça as etapas 1, 2 e 5. Com a chave, dá para acrescentar uma pergunta de política, como “Qual é a tolerância para atraso?”, e mostrar a resposta que cita o documento de origem. Se o bot estiver configurado e um barbeiro tiver feito `/vincular`, mostre também o aviso de nova reserva no Telegram dele.

## Versão curta

Para cerca de 15 minutos:

- junte os slides 2 e 3 em uma única fala de 1,5 min;
- reduza o slide 4 a 1 min, citando só RAG e alucinação;
- faça a demonstração em 2 min, só com as etapas 1, 2 e 4 (ou 1, 2 e 5, sem a chave da OpenAI);
- reduza o slide 10 a 1 min e junte os slides 11 e 12 em uma fala de 1 min.

## Gravação e publicação

- Grave em 1080p, com a tela inteira e o áudio de quem fala. Teste o microfone antes.
- Deixe o navegador em 100% de zoom. Feche notificações e abas com dados pessoais.
- Mantenha apenas dados fictícios na tela. Nenhuma chave, *token* ou telefone real pode aparecer.
- No YouTube, publique como **Público** ou **Não listado**, nunca como Privado, e confira o link em uma janela anônima antes de enviar.
- Se o vídeo tiver um tempo máximo, confirme-o com a orientadora antes de gravar e use a versão curta se for preciso.
