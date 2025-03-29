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
import pandas as pd  # Importar a biblioteca pandas
import time  # Importar a biblioteca time

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

# Carregar as credenciais do Firebase e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    # Carregar credenciais do Firebase
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)

    # Carregar credenciais de e-mail
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]

except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase com as credenciais
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:  # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")


# Obter referência do Firestore
db = firestore.client() if firebase_admin._apps else None

# Dados básicos
# A lista de horários base será gerada dinamicamente na tabela

servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 7,
    "Barba": 15,
}

barbeiros = ["Lucas Borges", "Aluizio"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Converter a string de data para um objeto datetime.datetime
    data_obj = datetime.strptime(data, '%d/%m/%Y')

    @firestore.transactional
    def atualizar_agendamento(transaction):
        doc = agendamento_ref.get(transaction=transaction)
        if doc.exists:
            raise ValueError("Horário já ocupado.")
        transaction.set(agendamento_ref, {
            'nome': nome,
            'telefone': telefone,
            'servicos': servicos,
            'barbeiro': barbeiro,
            'data': data_obj,  # Salvar o objeto datetime.datetime no Firestore
            'horario': horario
        })

    transaction = db.transaction()
    try:
        atualizar_agendamento(transaction)
    except ValueError as e:
        st.error(f"Erro ao salvar agendamento: {e}")
    except Exception as e:
        st.error(f"Erro inesperado ao salvar agendamento: {e}")

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_data = doc.to_dict()
            # Verificar se a data é um objeto datetime antes de formatar
            if isinstance(agendamento_data['data'], datetime):
                agendamento_data['data'] = agendamento_data['data'].date().strftime('%d/%m/%Y')
            elif isinstance(agendamento_data['data'], str):
                # Se for string, tentamos converter para datetime
                try:
                    # Tentar converter de diferentes formatos comuns
                    try:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%Y-%m-%d').date().strftime('%d/%m/%Y')
                    except ValueError:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%d/%m/%Y').date().strftime('%d/%m/%Y')

                except ValueError:
                    st.error("Formato de data inválido no Firestore")
                    return None
            else:
                st.error("Formato de data inválido no Firestore")
                return None

            agendamento_ref.delete()
            return agendamento_data
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None

# Nova função para desbloquear o horário seguinte
def desbloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Modificação aqui
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['nome'] == "BLOQUEADO":
            print(f"Tentando excluir a chave: {chave_bloqueio}")
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")

# Função para verificar disponibilidade do horário no Firebase
@st.cache_data
def verificar_disponibilidade(data, horario, barbeiro=None):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    # Verificar agendamento regular
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Verificar horário bloqueado
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_agendamento = agendamento_ref.get()
        doc_bloqueio = bloqueio_ref.get()
        return not doc_agendamento.exists and not doc_bloqueio.exists
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False

# Função para verificar disponibilidade do horário e do horário seguinte
@retry.Retry()
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    horario_seguinte = (datetime.strptime(horario, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
    chave_agendamento_seguinte = f"{data}_{horario_seguinte}_{barbeiro}"
    agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)
    try:
        doc_seguinte = agendamento_ref_seguinte.get()
        return not doc_seguinte.exists
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False

# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    db.collection('agendamentos').document(chave_bloqueio).set({
        'nome': "BLOQUEADO",
        'telefone': "BLOQUEADO",
        'servicos': ["BLOQUEADO"],
        'barbeiro': barbeiro,
        'data': data,
        'horario': horario
    })


# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    verificar_disponibilidade.clear()

data_agendamento_obj = st.date_input("Data para visualizar disponibilidade", min_value=datetime.today(), key="data_input_widget", on_change=handle_date_change)
data_para_tabela = data_agendamento_obj.strftime('%d/%m/%Y')  # Formatar o objeto date

# Tabela de Disponibilidade (Renderizada com a data do session state) FORA do formulário
st.subheader("Disponibilidade dos Barbeiros")

html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

# Gerar horários base dinamicamente
data_obj_tabela = data_agendamento_obj # Usar o objeto date diretamente
dia_da_semana_tabela = data_obj_tabela.weekday()  # 0 = segunda, 6 = domingo
horarios_tabela = []
for h in range(8, 20):
    for m in (0, 30):
        horario_str = f"{h:02d}:{m:02d}"
        if dia_da_semana_tabela == 5:  # Sábado
            horarios_tabela.append(horario_str)
        elif dia_da_semana_tabela < 5:  # Segunda a Sexta
            horarios_tabela.append(horario_str)

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    for barbeiro in barbeiros:
        disponivel = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
        status = "Disponível"
        bg_color = "forestgreen"
        color_text = "white"

        hora_int = int(horario.split(':')[0])
        minuto_int = int(horario.split(':')[1])

        if dia_da_semana_tabela < 5:  # Segunda a Sexta
            if (hora_int == 11 and minuto_int >= 0 and hora_int < 12 and barbeiro != "Lucas Borges") or \
               (hora_int == 12 and minuto_int >= 0 and hora_int < 13) or \
               (hora_int == 13 and minuto_int >= 0 and hora_int < 14 and barbeiro != "Aluizio"):
                status = "Indisponível"
                bg_color = "orange"
            elif (hora_int == 11 and minuto_int >= 0 and hora_int < 12 and barbeiro == "Lucas Borges"):
                status = "Disponível" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "Ocupado"
                bg_color = "forestgreen" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "firebrick"
                color_text = "white"
            elif (hora_int == 13 and minuto_int >= 0 and hora_int < 14 and barbeiro == "Aluizio"):
                status = "Disponível" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "Ocupado"
                bg_color = "forestgreen" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "firebrick"
                color_text = "white"
            else:
                status = "Disponível" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "Ocupado"
                bg_color = "forestgreen" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "firebrick"
                color_text = "white"

        elif dia_da_semana_tabela == 5: # Sábado - Manter a lógica original
            status = "Disponível" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "Ocupado"
            bg_color = "forestgreen" if verificar_disponibilidade(data_para_tabela, horario, barbeiro) else "firebrick"
            color_text = "white"

        # Adicionando uma altura fixa para as células de dados
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
html_table += '</tr>'

html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data dentro do formulário
    data_agendamento = st.session_state.data_agendamento.strftime('%d/%m/%Y') # Formatar para string aqui

    dia_da_semana = datetime.strptime(data_agendamento, '%d/%m/%Y').weekday()
    if dia_da_semana < 5:
        horarios_base_agendamento = []
        for h in range(8, 20):
            for m in (0, 30):
                if h < 11 or h >= 14:
                    horarios_base_agendamento.append(f"{h:02d}:{m:02d}")
                elif h == 11 and m == 0:
                    horarios_base_agendamento.append(f"{h:02d}:{m:02d}")
                elif h == 13 and m == 0:
                    horarios_base_agendamento.append(f"{h:02d}:{m:02d}")
    else:
        horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado
    horarios_filtrados = []
    for horario in horarios_base_agendamento:
        hora_int = int(horario.split(':')[0])
        minuto_int = int(horario.split(':')[1])
        if dia_da_semana < 5:
            if barbeiro_selecionado == "Lucas Borges":
                if not (hora_int == 12):
                    horarios_filtrados.append(horario)
            elif barbeiro_selecionado == "Aluizio":
                if not (hora_int == 12):
                    horarios_filtrados.append(horario)
            elif barbeiro_selecionado == "Sem preferência":
                if not (hora_int == 12):
                    horarios_filtrados.append(horario)
            else: # Para horários antes e depois do almoço
                horarios_filtrados.append(horario)
        else:
            horarios_filtrados.append(horario)

    horario_agendamento = st.selectbox("Horário", horarios_filtrados)  # Mantenha esta linha

    servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

    # Exibir os preços com o símbolo R$
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    st.write("Preços dos serviços:")
    for servico, preco in servicos_com_preco.items():
        st.write(f"{servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    with st.spinner("Processando agendamento..."):
        data_obj_agendamento = datetime.strptime(data_agendamento, '%d/%m/%Y')
        dia_da_semana_agendamento = data_obj_agendamento.weekday()
        hora_agendamento_int = int(horario_agendamento.split(':')[0])
        minuto_agendamento_int = int(horario_agendamento.split(':')[1])

        if dia_da_semana_agendamento < 5:  # Segunda a Sexta
            if (hora_agendamento_int == 11 and minuto_agendamento_int >= 0 and hora_agendamento_int < 12 and barbeiro_selecionado != "Lucas Borges") or \
               (hora_agendamento_int == 12 and minuto_agendamento_int >= 0 and hora_agendamento_int < 13) or \
               (hora_agendamento_int == 13 and minuto_agendamento_int >= 0 and hora_agendamento_int < 14 and barbeiro_selecionado != "Aluizio"):
                st.error("Barbeiro em horário de almoço")
                st.stop()  # Impede que o restante do código de agendamento seja executado
            elif barbeiro_selecionado == "Sem preferência":
                lucas_indisponivel = (hora_agendamento_int == 11 and minuto_agendamento_int >= 0 and hora_agendamento_int < 12) or \
                                    (hora_agendamento_int == 12 and minuto_agendamento_int >= 0 and hora_agendamento_int < 13) or \
                                    (hora_agendamento_int == 13 and minuto_agendamento_int >= 0 and hora_agendamento_int < 14 and barbeiros[0] != "Aluizio") # Lucas não é Aluizio

                aluizio_indisponivel = (hora_agendamento_int == 11 and minuto_agendamento_int >= 0 and hora_agendamento_int < 12 and barbeiros[1] != "Lucas Borges") or \
                                      (hora_agendamento_int == 12 and minuto_agendamento_int >= 0 and hora_agendamento_int < 13) or \
                                      (hora_agendamento_int == 13 and minuto_agendamento_int >= 0 and hora_agendamento_int < 14)

                if (hora_agendamento_int == 11 and minuto_agendamento_int >= 0 and hora_agendamento_int < 12 and barbeiros[0] != "Lucas Borges" and barbeiros[1] != "Lucas Borges") or \
                   (hora_agendamento_int == 12 and minuto_agendamento_int >= 0 and hora_agendamento_int < 13) or \
                   (hora_agendamento_int == 13 and minuto_agendamento_int >= 0 and hora_agendamento_int < 14 and barbeiros[1] != "Aluizio" and barbeiros[0] != "Aluizio"):
                    st.error("Barbeiros em horário de almoço")
                    st.stop()

        if nome and telefone and servicos_selecionados:
            if "Sem preferência" in barbeiro_selecionado:
                # Verifica se ambos os barbeiros estão ocupados
                if not verificar_disponibilidade(data_agendamento, horario_agendamento, barbeiros[0]) and not verificar_disponibilidade(data_agendamento, horario_agendamento, barbeiros[1]):
                    st.error("Horário indisponível para todos os barbeiros. Por favor, selecione outro horário.")
                else:
                    # Seleciona um barbeiro aleatoriamente que esteja disponível
                    barbeiros_disponiveis = [b for b in barbeiros if verificar_disponibilidade(data_agendamento, horario_agendamento, b)]
                    if barbeiros_disponiveis:
                        barbeiro_selecionado = random.choice(barbeiros_disponiveis)
                        if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                            if verificar_disponibilidade_horario_seguinte(data_agendamento, horario_agendamento, barbeiro_selecionado):
                                resumo = f"""
                                Nome: {nome}
                                Telefone: {telefone}
                                Data: {data_agendamento}
                                Horário: {horario_agendamento}
                                Barbeiro: {barbeiro_selecionado}
                                Serviços: {', '.join(servicos_selecionados)}
                                """
                                salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_selecionado)
                                # Bloquear o horário seguinte para o barbeiro selecionado
                                horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                                chave_bloqueio = f"{data_agendamento}_{horario_seguinte}_{barbeiro_selecionado}_BLOQUEADO" # Modificação aqui
                                db.collection('agendamentos').document(chave_bloqueio).set({
                                    'nome': "BLOQUEADO",
                                    'telefone': "BLOQUEADO",
                                    'servicos': ["BLOQUEADO"],
                                    'barbeiro': barbeiro_selecionado,
                                    'data': data_agendamento,
                                    'horario': horario_seguinte
                                })
                                enviar_email("Agendamento Confirmado", resumo)
                                verificar_disponibilidade.clear()
                                st.success("Agendamento confirmado com sucesso! Horário seguinte bloqueado.")
                                st.info("Resumo do agendamento:\n" + resumo)
                                st.info(f"O horário das {horario_seguinte} de {barbeiro_selecionado} foi bloqueado.")
                                time.sleep(5)
                                st.rerun()
                            else:
                                horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                                st.error(f"O barbeiro {barbeiro_selecionado} não poderá atender para corte e barba pois no horário seguinte ({horario_seguinte}) ele já está ocupado. Por favor, verifique outro barbeiro ou horário.")
                        else:
                            resumo = f"""
                            Nome: {nome}
                            Telefone: {telefone}
                            Data: {data_agendamento}
                            Horário: {horario_agendamento}
                            Barbeiro: {barbeiro_selecionado}
                            Serviços: {', '.join(servicos_selecionados)}
                            """
                            salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_selecionado)
                            enviar_email("Agendamento Confirmado", resumo)
                            verificar_disponibilidade.clear()
                            st.success("Agendamento confirmado com sucesso!")
                            st.info("Resumo do agendamento:\n" + resumo)
                            time.sleep(5)
                            st.rerun()
                else:
                    st.error("Horário indisponível para todos os barbeiros. Por favor, selecione outro horário.")
            else:
                if verificar_disponibilidade(data_agendamento, horario_agendamento, barbeiro_selecionado):
                    if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                        if verificar_disponibilidade_horario_seguinte(data_agendamento, horario_agendamento, barbeiro_selecionado):
                            resumo = f"""
                            Nome: {nome}
                            Telefone: {telefone}
                            Data: {data_agendamento}
                            Horário: {horario_agendamento}
                            Barbeiro: {barbeiro_selecionado}
                            Serviços: {', '.join(servicos_selecionados)}
                            """
                            salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_selecionado)
                            # Bloquear o horário seguinte para o barbeiro selecionado
                            horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                            chave_bloqueio = f"{data_agendamento}_{horario_seguinte}_{barbeiro_selecionado}_BLOQUEADO" # Modificação aqui
                            db.collection('agendamentos').document(chave_bloqueio).set({
                                'nome': "BLOQUEADO",
                                'telefone': "BLOQUEADO",
                                'servicos': ["BLOQUEADO"],
                                'barbeiro': barbeiro_selecionado,
                                'data': data_agendamento,
                                'horario': horario_seguinte
                            })
                            enviar_email("Agendamento Confirmado", resumo)
                            verificar_disponibilidade.clear()
                            st.success("Agendamento confirmado com sucesso! Horário seguinte bloqueado.")
                            st.info("Resumo do agendamento:\n" + resumo)
                            st.info(f"O horário das {horario_seguinte} de {barbeiro_selecionado} foi bloqueado.")
                            time.sleep(5)
                            st.rerun()
                        else:
                            horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                            st.error(f"O barbeiro {barbeiro_selecionado} não poderá atender para corte e barba pois no horário seguinte ({horario_seguinte}) ele já está ocupado. Por favor, verifique outro barbeiro ou horário.")
                    else:
                        resumo = f"""
                        Nome: {nome}
                        Telefone: {telefone}
                        Data: {data_agendamento}
                        Horário: {horario_agendamento}
                        Barbeiro: {barbeiro_selecionado}
                        Serviços: {', '.join(servicos_selecionados)}
                        """
                        salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_selecionado)
                        enviar_email("Agendamento Confirmado", resumo)
                        verificar_disponibilidade.clear()
                        st.success("Agendamento confirmado com sucesso!")
                        st.info("Resumo do agendamento:\n" + resumo)
                        time.sleep(5)
                        st.rerun()
                else:
                    st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário ou veja outro barbeiro.")
        else:
            st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")

# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone para Cancelamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today()) # Manter como objeto date
    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_agendamento) # Usar a lista correta aqui
    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros) # Adicionando a seleção do barbeiro
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")
    if submitted_cancelar:
        with st.spinner("Processando cancelamento..."):
            data_cancelar_str = data_cancelar.strftime('%d/%m/%Y') # Formatar para string aqui
            cancelado = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

        if cancelado:
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            enviar_email("Agendamento Cancelado", resumo_cancelamento)
            verificar_disponibilidade.clear()
            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
            # Verificar se o horário seguinte estava bloqueado e desbloqueá-lo
            if "Barba" in cancelado['servicos'] and any(corte in cancelado['servicos'] for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                horario_seguinte = (datetime.strptime(cancelado['horario'], '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                # Adicione estas linhas temporariamente para verificar os valores
                desbloquear_horario(cancelado['data'], horario_seguinte, cancelado['barbeiro'])
                st.info("O horário seguinte foi desbloqueado.")
            time.sleep(5)
            st.rerun()
        else:
            st.error(f"Não há agendamento para o telefone informado nesse horário e com o barbeiro selecionado.")
