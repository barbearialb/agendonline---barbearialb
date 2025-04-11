import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time

st.markdown(
    """
    <style>
        table {
            display: block !important;
            width: fit-content !important; /* Ou tente width: -webkit-fill-available !important; */
        }
        div[data-testid="stForm"] {
            display: block !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Configuração do Firebase e E-mail ---
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None
try:
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]
except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")

db = firestore.client() if firebase_admin._apps else None

# --- Dados Básicos ---
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Pezim": 7,  # Nosso serviço especial!
    "Navalhado": 25,
    "Barba": 15,
    "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}
lista_servicos = list(servicos.keys())
barbeiros = ["Lucas Borges", "Aluizio"]

# --- Constantes para Estados e Cores ---
ESTADO_DISPONIVEL = "Disponível"
ESTADO_OCUPADO = "Ocupado"
ESTADO_PEZIM_AGENDADO = "Pezim_Agendado" # Novo estado
ESTADO_INDISPONIVEL = "Indisponível" # Para almoço/bloqueio manual

COR_DISPONIVEL = "forestgreen"
COR_OCUPADO = "firebrick"
COR_PEZIM = "darkblue"         # Nova cor
COR_INDISPONIVEL = "orange"

# --- Listas de Serviços para Regras do Pezim ---
PEZIM = "Pezim"
SERVICOS_PERMITIDOS_COM_PEZIM = ["Pezim", "Tradicional", "Barba", "Social"]
SERVICOS_BLOQUEADOS_COM_PEZIM = ["Degradê", "Navalhado", "Abordagem de visagismo", "Consultoria de visagismo"]
SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE = ["Tradicional", "Social", "Degradê", "Navalhado"] # Para combinar com barba

# --- Funções ---

def enviar_email(assunto, mensagem):
    # (código da função inalterado)
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")


# MODIFICAÇÃO: Função para obter o estado detalhado do horário
# @st.cache_data # CUIDADO: Cache pode não refletir o estado imediatamente após uma ação. Remover se causar problemas.
def obter_estado_horario(data, horario, barbeiro):
    """
    Verifica o estado de um horário específico no Firestore.

    Retorna:
        str: Um dos estados: ESTADO_DISPONIVEL, ESTADO_OCUPADO, ESTADO_PEZIM_AGENDADO, ESTADO_INDISPONIVEL.
        dict: Os dados do agendamento, se houver (útil para atualizações). Retorna None se não houver agendamento.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return ESTADO_INDISPONIVEL, None # Considerar indisponível se DB falhar

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Para bloqueio de horário seguinte

    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_bloqueio = bloqueio_ref.get()
        if doc_bloqueio.exists:
            return ESTADO_INDISPONIVEL, None # Horário bloqueado explicitamente

        doc_agendamento = agendamento_ref.get()
        if doc_agendamento.exists:
            dados_agendamento = doc_agendamento.to_dict()
            servicos_agendados = dados_agendamento.get('servicos', [])

            # Verificar se é apenas Pezim ou se já está Ocupado
            if len(servicos_agendados) == 1 and servicos_agendados[0] == PEZIM:
                 # MODIFICAÇÃO: Adicionando verificação do campo 'status_horario' se ele existir
                 if dados_agendamento.get('status_horario') == ESTADO_PEZIM_AGENDADO:
                    return ESTADO_PEZIM_AGENDADO, dados_agendamento
                 else:
                     # Se não tem status, mas só tem Pezim, consideramos Pezim Agendado (compatibilidade)
                     # Ou talvez marcar como ocupado se o status não existir? Decidi por Pezim Agendado.
                     return ESTADO_PEZIM_AGENDADO, dados_agendamento
            else:
                # Se tem mais de um serviço, ou um serviço diferente de Pezim, está ocupado
                return ESTADO_OCUPADO, dados_agendamento
        else:
            # Nenhuma das chaves existe, horário está disponível
            return ESTADO_DISPONIVEL, None

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário: {e}")
        return ESTADO_INDISPONIVEL, None
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return ESTADO_INDISPONIVEL, None

# MODIFICAÇÃO: Função de salvar agendamento mais robusta
def salvar_ou_atualizar_agendamento(data, horario, nome, telefone, servicos_novos, barbeiro, estado_atual, dados_atuais=None):
    """
    Salva um novo agendamento ou atualiza um existente (caso de adicionar a um Pezim).
    Usa transação para garantir atomicidade.
    """
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    data_obj = datetime.strptime(data, '%d/%m/%Y') # Converter string para datetime

    transaction = db.transaction()

    @firestore.transactional
    def processar_agendamento(transaction):
        doc_snapshot = agendamento_ref.get(transaction=transaction) # Ler DENTRO da transação

        if estado_atual == ESTADO_DISPONIVEL:
            if doc_snapshot.exists:
                 # Segurança extra: Alguém agendou enquanto o usuário preenchia o form
                 raise ValueError("Ops! Este horário foi ocupado enquanto você preenchia. Tente novamente.")
            # Criar novo agendamento
            if len(servicos_novos) == 1 and servicos_novos[0] == PEZIM:
                status_horario = ESTADO_PEZIM_AGENDADO # Status inicial do Pezim
            else:
                status_horario = ESTADO_OCUPADO # Ocupado para outros serviços ou múltiplos

            dados_para_salvar = {
                'nome': nome, 'telefone': telefone, 'servicos': servicos_novos,
                'barbeiro': barbeiro, 'data': data_obj, 'horario': horario,
                'status_horario': status_horario # Salvar o status
            }
            transaction.set(agendamento_ref, dados_para_salvar)
            return dados_para_salvar, None # Retorna os dados salvos

        elif estado_atual == ESTADO_PEZIM_AGENDADO:
            if not doc_snapshot.exists:
                 # Segurança extra: Agendamento Pezim foi cancelado enquanto usuário preenchia
                 raise ValueError("Ops! O agendamento 'Pezim' neste horário foi cancelado. Tente novamente.")

            dados_existentes = doc_snapshot.to_dict()
            servicos_existentes = dados_existentes.get('servicos', [])

            # Adicionar novos serviços (evitando duplicatas se Pezim for selecionado novamente)
            servicos_combinados = list(set(servicos_existentes + servicos_novos))

            # Verificar se os NOVOS serviços adicionados são permitidos
            servicos_realmente_adicionados = [s for s in servicos_novos if s not in servicos_existentes]
            if any(s in SERVICOS_BLOQUEADOS_COM_PEZIM for s in servicos_realmente_adicionados):
                 raise ValueError(f"Com o {PEZIM}, só pode agendar {', '.join(SERVICOS_PERMITIDOS_COM_PEZIM)}")

            # Atualizar o agendamento para Ocupado
            dados_para_atualizar = {
                'servicos': servicos_combinados,
                'status_horario': ESTADO_OCUPADO # Mudar status para ocupado
                # Manter nome, telefone, etc., do agendamento original do Pezim?
                # Ou atualizar com os dados do novo cliente? Decidi manter o original.
                # Se quiser atualizar, descomente abaixo:
                # 'nome': nome,
                # 'telefone': telefone,
            }
            transaction.update(agendamento_ref, dados_para_atualizar)
            # Retorna os dados atualizados e os originais (caso precise do nome/tel original)
            dados_completos_atualizados = {**dados_existentes, **dados_para_atualizar}
            return dados_completos_atualizados, dados_existentes

        elif estado_atual == ESTADO_OCUPADO:
             raise ValueError("Horário já ocupado.")
        elif estado_atual == ESTADO_INDISPONIVEL:
             raise ValueError("Horário indisponível (almoço ou bloqueado).")
        else:
             raise ValueError("Estado do horário desconhecido.")

    try:
        dados_finais, dados_originais_pezim = processar_agendamento(transaction)
        return dados_finais, dados_originais_pezim # Sucesso
    except ValueError as e:
        st.error(f"{e}")
        return None, None
    except Exception as e:
        st.error(f"Erro inesperado ao salvar/atualizar agendamento: {e}")
        return None, None

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    # (código da função com formatação de data - parece ok, mas atenção aos formatos)
    # Adicionar limpeza do status_horario se existir? Não, a exclusão remove tudo.
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_data = doc.to_dict()
            # Tratamento de data (manter como está por enquanto)
            if isinstance(agendamento_data.get('data'), datetime):
                 agendamento_data['data_str'] = agendamento_data['data'].date().strftime('%d/%m/%Y')
            elif isinstance(agendamento_data.get('data'), str):
                 try:
                     # Tentar converter de diferentes formatos comuns
                     try:
                         data_dt = datetime.strptime(agendamento_data['data'], '%Y-%m-%d')
                     except ValueError:
                          data_dt = datetime.strptime(agendamento_data['data'], '%d/%m/%Y')
                     agendamento_data['data_str'] = data_dt.date().strftime('%d/%m/%Y')
                 except ValueError:
                    st.warning("Formato de data no Firestore não reconhecido para exibição.")
                    agendamento_data['data_str'] = agendamento_data['data'] # Exibe como está
            else:
                st.warning("Tipo de data inválido no Firestore.")
                agendamento_data['data_str'] = str(agendamento_data.get('data')) # Tenta exibir como string

            agendamento_ref.delete()
            return agendamento_data # Retorna os dados do agendamento cancelado
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore para cancelamento: {e}")
        return None


# Nova função para desbloquear o horário seguinte
def desbloquear_horario(data, horario, barbeiro):
     # (código da função inalterado)
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Modificação aqui
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO": # Usar .get() para segurança
            print(f"Tentando excluir a chave: {chave_bloqueio}")
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")


# Função para verificar disponibilidade do horário seguinte (usada para Barba+Corte)
# @retry.Retry() # Retry pode ser útil, mas pode mascarar problemas temporários
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Usa a nova função para checar o estado detalhado
        estado_seguinte, _ = obter_estado_horario(data, horario_seguinte_str, barbeiro)

        # Considera disponível se for DISPONIVEL ou se for PEZIM_AGENDADO
        # (porque podemos sobrescrever um Pezim se necessário, embora essa função seja só pra checar)
        # Mas para Barba+Corte, precisamos que esteja realmente livre.
        return estado_seguinte == ESTADO_DISPONIVEL

    except ValueError:
         st.error(f"Formato de horário inválido: {horario}")
         return False
    # Remover retry daqui e deixar a função principal tratar erros de conexão
    # except google.api_core.exceptions.RetryError as e:
    #     st.error(f"Erro de conexão com o Firestore (horário seguinte): {e}")
    #     return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar horário seguinte: {e}")
        return False


# Função para bloquear horário (usada para Barba+Corte)
def bloquear_horario_seguinte(data, horario_atual, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_atual_dt = datetime.strptime(horario_atual, '%H:%M')
        horario_seguinte_dt = horario_atual_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Converter data string para objeto datetime para salvar no Firestore
        try:
             data_obj = datetime.strptime(data, '%d/%m/%Y')
        except ValueError:
             st.error("Formato de data inválido ao tentar bloquear horário.")
             return False


        chave_bloqueio = f"{data}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj, # Salvar como datetime
            'horario': horario_seguinte_str,
            'status_horario': ESTADO_INDISPONIVEL # Adicionar status
        })
        st.info(f"O horário das {horario_seguinte_str} de {barbeiro} foi bloqueado.")
        return True
    except ValueError:
         st.error(f"Formato de horário inválido ao tentar bloquear: {horario_atual}")
         return False
    except Exception as e:
        st.error(f"Erro ao bloquear horário seguinte: {e}")
        return False

# --- Interface Streamlit ---
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget
    # obter_estado_horario.clear() # Limpar cache se estiver usando @st.cache_data

data_agendamento_obj = st.date_input("Data para visualizar disponibilidade", min_value=datetime.today().date(), key="data_input_widget", on_change=handle_date_change, value=st.session_state.data_agendamento) # Usar value
data_para_tabela = data_agendamento_obj.strftime('%d/%m/%Y')

# --- Tabela de Disponibilidade ---
st.subheader("Disponibilidade dos Barbeiros")

# Gerar HTML da tabela (MODIFICADO para usar obter_estado_horario)
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

data_obj_tabela = data_agendamento_obj
dia_da_semana_tabela = data_obj_tabela.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

# Placeholder para a tabela enquanto carrega
table_placeholder = st.empty()
table_placeholder.markdown("Carregando disponibilidade...")

# Construir a tabela
tabela_renderizada = False # Flag para evitar renderização duplicada
try:
    linhas_html = ""
    for horario in horarios_tabela:
        linhas_html += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
        for barbeiro in barbeiros:
            status_texto = ""
            bg_color = ""
            color_text = "white"

            # Obter estado detalhado do horário
            estado_horario, _ = obter_estado_horario(data_para_tabela, horario, barbeiro) # Ignora os dados aqui

            hora_int = int(horario.split(':')[0])
            minuto_int = int(horario.split(':')[1])

            # Lógica de Horário de Almoço (PRECEDE a verificação do Firestore)
            horario_almoco = False
            if dia_da_semana_tabela < 5: # Segunda a Sexta
                # Almoço Aluizio: 11:00 - 11:59
                if barbeiro == "Aluizio" and hora_int == 11:
                    horario_almoco = True
                # Almoço Lucas: 13:00 - 13:59
                elif barbeiro == "Lucas Borges" and hora_int == 13:
                    horario_almoco = True
                 # Horário 12:00 - 12:59: Indisponível para ambos
                elif hora_int == 12:
                    horario_almoco = True

            if horario_almoco:
                status_texto = ESTADO_INDISPONIVEL
                bg_color = COR_INDISPONIVEL
            else:
                # Se não for almoço, verifica o estado do Firestore
                if estado_horario == ESTADO_DISPONIVEL:
                    status_texto = "Disponível"
                    bg_color = COR_DISPONIVEL
                elif estado_horario == ESTADO_PEZIM_AGENDADO:
                    status_texto = "Pezim (Rápido)" # Novo texto
                    bg_color = COR_PEZIM         # Nova cor
                elif estado_horario == ESTADO_OCUPADO:
                    status_texto = "Ocupado"
                    bg_color = COR_OCUPADO
                elif estado_horario == ESTADO_INDISPONIVEL: # Pode ser bloqueio do horário seguinte
                     status_texto = "Indisponível"
                     bg_color = COR_INDISPONIVEL
                else: # Fallback
                    status_texto = "Erro"
                    bg_color = "gray"

            linhas_html += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status_texto}</td>'
        linhas_html += '</tr>'

    html_table += linhas_html
    html_table += '</table>'
    table_placeholder.markdown(html_table, unsafe_allow_html=True)
    tabela_renderizada = True

except Exception as e:
     st.error(f"Erro ao gerar a tabela de disponibilidade: {e}")
     if not tabela_renderizada:
         table_placeholder.markdown("Erro ao carregar disponibilidade.")


# --- Formulário de Agendamento ---
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone (com DDD, ex: 11987654321)") # Instrução adicionada

    # A data é pega do seletor fora do form
    data_agendamento_str = st.session_state.data_agendamento.strftime('%d/%m/%Y')
    st.write(f"Data selecionada: **{data_agendamento_str}**") # Mostrar data selecionada

    horarios_disponiveis_form = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    horario_agendamento = st.selectbox("Horário", horarios_disponiveis_form)

    barbeiro_selecionado_form = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir preços
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    with st.expander("Ver Preços dos Serviços"):
        for servico, preco in servicos_com_preco.items():
            st.write(f"- {servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    # --- Lógica de Processamento do Agendamento ---
    with st.spinner("Verificando e processando agendamento..."):
        erro_agendamento = False
        mensagem_erro = ""

        # 1. Validações Iniciais
        if not nome or not telefone or not servicos_selecionados or not horario_agendamento or not barbeiro_selecionado_form:
            mensagem_erro = "Por favor, preencha todos os campos (Nome, Telefone, Horário, Barbeiro) e selecione pelo menos 1 serviço."
            erro_agendamento = True

        # Validar formato do telefone (simples)
        if not erro_agendamento and not (telefone.isdigit() and len(telefone) >= 10):
             mensagem_erro = "Formato de telefone inválido. Use apenas números, incluindo o DDD (mínimo 10 dígitos)."
             erro_agendamento = True

        if not erro_agendamento:
            # 2. Determinar o Barbeiro Real
            barbeiro_final = None
            barbeiros_a_checar = []

            if barbeiro_selecionado_form == "Sem preferência":
                barbeiros_a_checar = barbeiros # Checar ambos
            else:
                barbeiros_a_checar = [barbeiro_selecionado_form] # Checar apenas o selecionado

            # 3. Verificar Disponibilidade e Regras para cada barbeiro potencial
            barbeiro_disponivel_encontrado = False
            for b_check in barbeiros_a_checar:
                 # Obter estado ATUALIZADO do horário ANTES de tentar salvar
                 # Não usar cache aqui para garantir dados frescos
                 estado_horario_atual, dados_atuais = obter_estado_horario(data_agendamento_str, horario_agendamento, b_check)

                 # --- Aplicar Regras de Negócio ---

                 # a) Horário de Almoço (Checado primeiro)
                 data_obj_agendamento = datetime.strptime(data_agendamento_str, '%d/%m/%Y')
                 dia_da_semana_agendamento = data_obj_agendamento.weekday()
                 hora_agendamento_int = int(horario_agendamento.split(':')[0])
                 almoco_barbeiro = False
                 if dia_da_semana_agendamento < 5: # Seg-Sex
                     if (b_check == "Aluizio" and hora_agendamento_int == 11) or \
                        (b_check == "Lucas Borges" and hora_agendamento_int == 13) or \
                        (hora_agendamento_int == 12):
                         almoco_barbeiro = True

                 if almoco_barbeiro:
                      # Se for sem preferência, apenas continua para o próximo barbeiro
                      if barbeiro_selecionado_form != "Sem preferência":
                           mensagem_erro = f"Barbeiro {b_check} está em horário de almoço ({horario_agendamento})."
                           erro_agendamento = True
                      continue # Pula para o próximo barbeiro se for sem preferência

                 # b) Visagismo só com Lucas
                 servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
                 if any(s in servicos_selecionados for s in servicos_visagismo) and b_check != "Lucas Borges":
                     if barbeiro_selecionado_form != "Sem preferência":
                          mensagem_erro = "Apenas Lucas Borges realiza atendimentos de visagismo."
                          erro_agendamento = True
                     continue # Pula para o próximo barbeiro

                 # c) Regras do Pezim e Ocupação Geral
                 if estado_horario_atual == ESTADO_OCUPADO:
                      if barbeiro_selecionado_form != "Sem preferência":
                           mensagem_erro = f"Horário {horario_agendamento} já ocupado para {b_check}."
                           erro_agendamento = True
                      continue # Pula para o próximo barbeiro

                 elif estado_horario_atual == ESTADO_INDISPONIVEL:
                      if barbeiro_selecionado_form != "Sem preferência":
                           mensagem_erro = f"Horário {horario_agendamento} indisponível para {b_check} (bloqueado)."
                           erro_agendamento = True
                      continue # Pula para o próximo barbeiro

                 elif estado_horario_atual == ESTADO_PEZIM_AGENDADO:
                      # Horário tem Pezim. Verificar se o NOVO serviço é permitido.
                      if any(s in SERVICOS_BLOQUEADOS_COM_PEZIM for s in servicos_selecionados if s != PEZIM): # Ignora se Pezim foi selecionado de novo
                           if barbeiro_selecionado_form != "Sem preferência":
                                mensagem_erro = f"Com o Pezim, só pode agendar Tradicional, Barba, Social ou outro Pezim neste horário ({horario_agendamento}) com {b_check}."
                                erro_agendamento = True
                           continue # Pula para o próximo barbeiro
                      # Se chegou aqui, a combinação com Pezim é VÁLIDA para este barbeiro

                 elif estado_horario_atual == ESTADO_DISPONIVEL:
                       # Se está disponível, verificar se a combinação Pezim + Bloqueado está sendo feita agora
                       if PEZIM in servicos_selecionados and any(s in SERVICOS_BLOQUEADOS_COM_PEZIM for s in servicos_selecionados):
                            if barbeiro_selecionado_form != "Sem preferência":
                                 mensagem_erro = f"Não é possível agendar Pezim junto com Degradê, Navalhado ou Visagismo no mesmo horário ({horario_agendamento}) com {b_check}."
                                 erro_agendamento = True
                            continue # Pula para o próximo barbeiro
                       # Se chegou aqui, o horário está disponível e a combinação é VÁLIDA para este barbeiro

                 # d) Regra Barba + Corte (Bloquear Horário Seguinte)
                 precisa_bloquear_seguinte = False
                 if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE):
                     if not verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, b_check):
                          # Horário seguinte NÃO está disponível
                          horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                          horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                          if barbeiro_selecionado_form != "Sem preferência":
                               mensagem_erro = f"Não é possível agendar Corte+Barba às {horario_agendamento} com {b_check}, pois o horário seguinte ({horario_seguinte_str}) já está ocupado/indisponível."
                               erro_agendamento = True
                          continue # Pula para o próximo barbeiro
                     else:
                          # Horário seguinte está disponível, precisa bloquear
                          precisa_bloquear_seguinte = True

                 # --- Se passou por todas as validações para este barbeiro ---
                 barbeiro_final = b_check
                 barbeiro_disponivel_encontrado = True
                 break # Encontrou um barbeiro válido, sai do loop

            # 4. Finalizar o Agendamento se um barbeiro foi encontrado
            if not erro_agendamento and barbeiro_disponivel_encontrado:
                # Obter o estado final e dados atuais para passar para a função de salvar/atualizar
                estado_final_check, dados_atuais_final = obter_estado_horario(data_agendamento_str, horario_agendamento, barbeiro_final)

                # Tentar salvar ou atualizar no Firestore
                dados_agendamento_final, _ = salvar_ou_atualizar_agendamento(
                    data_agendamento_str, horario_agendamento, nome, telefone,
                    servicos_selecionados, barbeiro_final,
                    estado_final_check, dados_atuais_final
                )

                if dados_agendamento_final:
                    # Sucesso ao salvar/atualizar
                    # Bloquear horário seguinte, se necessário
                    if precisa_bloquear_seguinte:
                        bloquear_horario_seguinte(data_agendamento_str, horario_agendamento, barbeiro_final)

                    # Preparar e enviar e-mail
                    resumo = f"""
                    Agendamento Confirmado:
                    Nome: {dados_agendamento_final['nome']}
                    Telefone: {dados_agendamento_final['telefone']}
                    Data: {data_agendamento_str}
                    Horário: {horario_agendamento}
                    Barbeiro: {barbeiro_final}
                    Serviços: {', '.join(dados_agendamento_final['servicos'])}
                    """
                    enviar_email("Agendamento Confirmado - Barbearia LB", resumo)

                    # Limpar cache e exibir sucesso
                    # obter_estado_horario.clear() # Limpa cache se estiver ativo
                    st.success("Agendamento confirmado com sucesso!")
                    st.info("Resumo do agendamento:\n" + resumo)
                    if precisa_bloquear_seguinte:
                         st.info(f"O horário seguinte foi bloqueado para {barbeiro_final}.")

                    # Adiar rerun para permitir leitura da mensagem
                    time.sleep(6)
                    st.rerun()
                else:
                    # Função salvar_ou_atualizar_agendamento já exibiu o erro
                    erro_agendamento = True
                    # mensagem_erro já foi definida dentro da função salvar_ou_atualizar

            elif not erro_agendamento and not barbeiro_disponivel_encontrado:
                 # Passou as validações iniciais, mas nenhum barbeiro estava disponível/válido
                 if barbeiro_selecionado_form == "Sem preferência":
                      mensagem_erro = f"Nenhum barbeiro disponível ou válido para os serviços selecionados no horário {horario_agendamento}. Verifique os horários de almoço ou tente outro horário/serviço."
                 else:
                      # Se um barbeiro específico foi selecionado, o erro já deve ter sido setado no loop
                      if not mensagem_erro: # Fallback
                           mensagem_erro = f"Não foi possível agendar com {barbeiro_selecionado_form} às {horario_agendamento}. Verifique a disponibilidade ou regras de serviço."
                 erro_agendamento = True

        # Exibir mensagem de erro final, se houver
        if erro_agendamento and mensagem_erro:
            st.error(mensagem_erro)
        elif erro_agendamento: # Caso algum erro tenha ocorrido sem mensagem específica
             st.error("Ocorreu um erro ao processar o agendamento. Por favor, tente novamente.")


# --- Formulário de Cancelamento ---
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Seu Telefone (usado no agendamento)")
    data_cancelar_obj = st.date_input("Data do Agendamento a Cancelar", min_value=datetime.today().date()) # Objeto date

    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    horario_cancelar = st.selectbox("Horário do Agendamento a Cancelar", horarios_base_cancelamento)

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento a Cancelar", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    if submitted_cancelar:
        with st.spinner("Processando cancelamento..."):
            erro_cancelamento = False
            msg_erro_cancelamento = ""

            if not telefone_cancelar or not data_cancelar_obj or not horario_cancelar or not barbeiro_cancelar:
                 msg_erro_cancelamento = "Por favor, preencha todos os campos para cancelar."
                 erro_cancelamento = True
            elif not (telefone_cancelar.isdigit() and len(telefone_cancelar) >= 10):
                 msg_erro_cancelamento = "Formato de telefone inválido para cancelamento."
                 erro_cancelamento = True

            if not erro_cancelamento:
                data_cancelar_str = data_cancelar_obj.strftime('%d/%m/%Y') # Formatar para string
                cancelado_data = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

                if cancelado_data is not None:
                    # Sucesso no cancelamento
                    # obter_estado_horario.clear() # Limpar cache

                    # Verificar se precisa desbloquear horário seguinte
                    servicos_cancelados = cancelado_data.get('servicos', [])
                    if "Barba" in servicos_cancelados and any(corte in servicos_cancelados for corte in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE):
                        horario_seguinte_dt = (datetime.strptime(cancelado_data['horario'], '%H:%M') + timedelta(minutes=30))
                        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                        # Usar a data formatada que foi usada para cancelar
                        desbloquear_horario(data_cancelar_str, horario_seguinte_str, cancelado_data['barbeiro'])
                        st.info(f"O horário seguinte ({horario_seguinte_str}) foi desbloqueado.")

                    # Enviar email e mostrar sucesso
                    resumo_cancelamento = f"""
                    Agendamento Cancelado:
                    Nome: {cancelado_data.get('nome', 'N/A')}
                    Telefone: {cancelado_data.get('telefone', 'N/A')}
                    Data: {cancelado_data.get('data_str', data_cancelar_str)}
                    Horário: {cancelado_data.get('horario', 'N/A')}
                    Barbeiro: {cancelado_data.get('barbeiro', 'N/A')}
                    Serviços: {', '.join(servicos_cancelados)}
                    """
                    enviar_email("Agendamento Cancelado - Barbearia LB", resumo_cancelamento)
                    st.success("Agendamento cancelado com sucesso!")
                    st.info("Resumo do cancelamento:\n" + resumo_cancelamento)

                    time.sleep(6)
                    st.rerun()
                else:
                    # Falha no cancelamento (função já deve ter mostrado erro de acesso ao DB ou retornado None)
                    if not db:
                         msg_erro_cancelamento = "Erro de conexão com o banco de dados."
                    else:
                         msg_erro_cancelamento = f"Não foi encontrado agendamento para o telefone '{telefone_cancelar}' na data {data_cancelar_str}, horário {horario_cancelar} com o barbeiro {barbeiro_cancelar}. Verifique os dados."
                    erro_cancelamento = True
            # Exibir erro de cancelamento
            if erro_cancelamento:
                st.error(msg_erro_cancelamento)
