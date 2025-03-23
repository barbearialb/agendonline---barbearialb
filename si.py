import streamlit as st 
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random

# Carregar as credenciais do Firebase e e-mail a partir do Streamlit secrets
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

# Inicializar Firebase
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")

db = firestore.client() if firebase_admin._apps else None

# Dados básicos
horarios = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 5,
    "Barba": 15,
}
barbeiros = ["Lucas Borges", "Aluizio", "Sem preferência"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
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

# Salvar agendamento no Firestore
def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    try:
        # Verificar se já existe um agendamento para esse horário
        if agendamento_ref.get().exists:
            st.error("Já existe um agendamento para esse horário.")
            return None
        
        # Salvar o agendamento
        agendamento_ref.set({
            'nome': nome,
            'telefone': telefone,
            'servicos': servicos,
            'barbeiro': barbeiro,
            'data': data,
            'horario': horario
        })
        return True  # Sucesso

    except Exception as e:
        st.error(f"Erro ao salvar o agendamento: {e}")
        return None

# Cancelar agendamento
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_info = doc.to_dict()
            agendamento_ref.delete()

            # Recalcular os status dos horários após o cancelamento
            status_horarios_atualizado = obter_status_horarios(data)

            return agendamento_info, status_horarios_atualizado  # Retorna os dados cancelados e os novos status
        else:
            return None, None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None, None


def obter_status_horarios(data, barbeiro=None):
    horarios_status = {h: "disponivel" for h in horarios}
    ocupacoes = {h: [] for h in horarios}
    bloqueios_extra = {}
    agendamentos_distribuidos = {}

    # Bloquear horários entre 12h e 14h (exceto sábado)
    dia_semana = datetime.strptime(data, '%d/%m/%Y').weekday()
    if dia_semana < 5:  # Segunda a sexta
        for h in horarios:
            hora_int = int(h[:2])
            if 12 <= hora_int < 14:
                horarios_status[h] = "bloqueado"

    try:
        # Busca os agendamentos já existentes para o dia especificado
        docs = db.collection('agendamentos').where('data', '==', data).stream()

        for doc in docs:
            agendamento = doc.to_dict()
            h = agendamento['horario']
            b = agendamento['barbeiro']
            
            # Armazena quais barbeiros estão ocupados em cada horário
            ocupacoes[h].append(b)

            # Se o agendamento for corte + barba, bloqueia o próximo horário
            if len(agendamento['servicos']) == 2 and "Barba" in agendamento['servicos']:
                idx = horarios.index(h)
                if idx + 1 < len(horarios):
                    h_next = horarios[idx + 1]
                    if h_next not in bloqueios_extra:
                        bloqueios_extra[h_next] = []
                    bloqueios_extra[h_next].append(b)

        # Atualiza o status dos horários conforme a ocupação dos barbeiros
        for h in horarios:
            barbeiros_ocupados = ocupacoes[h]
            if len(barbeiros_ocupados) == len(barbeiros) - 1:
                horarios_status[h] = "ocupado"
            elif len(barbeiros_ocupados) > 0:
                horarios_status[h] = "parcial"

        # Atualiza o status para horários bloqueados extras (corte + barba)
        for h, bloqueados in bloqueios_extra.items():
            if len(bloqueados) == len(barbeiros) - 1:
                horarios_status[h] = "ocupado"
            else:
                horarios_status[h] = "parcial"

        # Agora, se um barbeiro for escolhido, atualizamos os horários
        if barbeiro:
            horarios_status = atualizar_status_barbeiro(horarios_status, barbeiro, horarios, ocupacoes)

        # Atribui barbeiros aos horários disponíveis
        if barbeiro is None:
            for h in horarios:
                if horarios_status[h] == "disponivel":
                    barbeiro_disponivel = random.choice([b for b in barbeiros if b not in ocupacoes[h]])
                    agendamentos_distribuidos[h] = barbeiro_disponivel
        else:
            for h in horarios:
                if horarios_status[h] == "disponivel":
                    agendamentos_distribuidos[h] = barbeiro  # Atribui o barbeiro especificado para o horário disponível

        return horarios_status, agendamentos_distribuidos

    except Exception as e:
        print(f"Erro ao obter status dos horários: {e}")
        return {}, {}  # Retorna dicionários vazios em caso de erro
    
# Função que atualiza o status dos horários conforme o barbeiro escolhido
def atualizar_status_barbeiro(horarios_status, barbeiro, horarios, ocupacoes):
    """
    Atualiza os status dos horários quando um barbeiro é escolhido ou alterado.
    """
    for h in horarios:
        if barbeiro in ocupacoes[h]:  # O barbeiro já está ocupado nesse horário
            horarios_status[h] = "ocupado"
        elif len(ocupacoes[h]) > 0:  # Caso haja outro barbeiro já agendado
            horarios_status[h] = "parcial"
        else:  # Caso o horário esteja disponível
            horarios_status[h] = "disponivel"
    return horarios_status

def atualizar_cor_disponibilidade(data, horario, barbeiro_selecionado, horarios_status):
    try:
        # Consulta ao Firestore para verificar se há agendamento no horário
        docs = db.collection('agendamentos').where('data', '==', data).where('horario', '==', horario).where('barbeiro', '==', barbeiro_selecionado).stream()

        # Verificar se há agendamentos para o horário e barbeiro selecionados
        if any(docs):  # Verifica se o iterador docs contém pelo menos um documento
            return "ocupado"  # O horário está ocupado, retorna "ocupado" (vermelho)
        else:
            return "disponivel"  # O horário está disponível, retorna "disponível" (verde)

    except Exception as e:
        print(f"Erro ao verificar a disponibilidade: {e}")
        return "erro"  # Retorna erro caso haja algum problema

# Função que lida com a seleção do barbeiro e horário
def selecionar_barbeiro_e_horario(data, horario, barbeiro_selecionado, horarios_status):
    horario_cor = atualizar_cor_disponibilidade(data, horario, barbeiro_selecionado, horarios_status)

    if horario_cor == "ocupado":
        print("Horário ocupado! Cor vermelha.")
    elif horario_cor == "disponivel":
        print("Horário disponível! Cor verde.")
    else:
        print("Erro ao verificar o horário.")

# Função de confirmação de agendamento
def confirmar_agendamento(data, horario, barbeiro_selecionado, nome, telefone):
    # Verificar a disponibilidade do horário
    if verificar_disponibilidade(data, horario):
        # Se o horário estiver disponível, prosseguir com o agendamento
        try:
            chave_agendamento = f"{data}_{horario}"
            agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

            # Salva o agendamento no Firestore
            agendamento_ref.set({
                'data': data,
                'horario': horario,
                'barbeiro': barbeiro_selecionado,
                'nome': nome,
                'telefone': telefone,
                'status': 'confirmado'
            })

            st.success(f"Agendamento confirmado para {horario} no dia {data}.")
        except Exception as e:
            st.error(f"Erro ao confirmar o agendamento: {e}")
    else:
        st.error(f"Não é possível agendar para o horário {horario} no dia {data}, pois ele está ocupado.")

# Função de verificação de disponibilidade
def verificar_disponibilidade(data, horario):
    try:
        chave_agendamento = f"{data}_{horario}"
        agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
        doc = agendamento_ref.get()

        if doc.exists:
            return False  # Horário já ocupado
        else:
            return True  # Horário disponível
    except Exception as e:
        print(f"Erro ao verificar a disponibilidade: {e}")
        return False  # Caso de erro ao verificar disponibilidade
    
def escolher_barbeiro(data, horario):
    """Escolhe um barbeiro disponível ou aleatoriamente se ambos estiverem livres."""
    docs = db.collection('agendamentos').where('data', '==', data).where('horario', '==', horario).stream()

    barbeiros_ocupados = {doc.to_dict()['barbeiro'] for doc in docs}
    barbeiros_disponiveis = [b for b in barbeiros if b != "Sem preferência" and b not in barbeiros_ocupados]

    if len(barbeiros_disponiveis) == 1:
        return barbeiros_disponiveis[0]  # Se um está livre, escolher automaticamente
    elif len(barbeiros_disponiveis) == 2:
        return random.choice(barbeiros_disponiveis)  # Se ambos estão livres, escolher aleatoriamente
    else:
        return None  # Se ambos estão ocupados

#Interface de Usuário
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/agendonline---barbearialb/blob/main/icone.png?raw=true", use_container_width=True)

# Aba Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data_obj = st.date_input("Data", min_value=datetime.today())
data = data_obj.strftime('%d/%m/%Y')
barbeiro = st.selectbox("Escolha o barbeiro", barbeiros)

if barbeiro != "Sem preferência":
    # Verificar os status dos horários para o barbeiro escolhido
    status_horarios, agendamentos_distribuidos = obter_status_horarios(data, barbeiro)

    # Mostrar horários com bolinhas coloridas
    horarios_disponiveis_para_selecao = []
    horarios_coloridos = []

    for h in horarios:
        status = status_horarios.get(h, "disponivel")  # Se 'h' não existir, assume 'disponivel'
        
        if status == "ocupado":
            horarios_coloridos.append(f"🔴 {h}")
        elif status == "parcial":
            horarios_coloridos.append(f"🟡 {h}")
            horarios_disponiveis_para_selecao.append(h)
        else:
            horarios_coloridos.append(f"🟢 {h}")
            horarios_disponiveis_para_selecao.append(h)

    horario_index = st.selectbox("Horário", list(range(len(horarios_coloridos))),
                                 format_func=lambda x: horarios_coloridos[x])
    horario = horarios[horario_index]

    # Chama a função para atualizar a cor do horário
    status_cor = atualizar_cor_disponibilidade(data, horario, barbeiro, status_horarios)
    
    # Mudar a cor dinamicamente dependendo do status
    if status_cor == "ocupado":
        st.markdown(f"<p style='color:red;'>Horário {horario} está ocupado.</p>", unsafe_allow_html=True)
    elif status_cor == "disponivel":
        st.markdown(f"<p style='color:green;'>Horário {horario} está disponível.</p>", unsafe_allow_html=True)
    elif status_cor == "erro":
        st.markdown(f"<p style='color:orange;'>Erro ao verificar disponibilidade.</p>", unsafe_allow_html=True)
    else:
        st.markdown(f"<p style='color:yellow;'>Escolha um barbeiro para atualizar a disponibilidade.</p>", unsafe_allow_html=True)

# Exibir preços
servicos_com_preco = {s: f"R$ {p}" for s, p in servicos.items()}
st.write("Preços dos serviços:")
for s, p in servicos_com_preco.items():
    st.write(f"{s}: {p}")

# Seleção de serviços
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Validação e Agendamento
if st.button("Confirmar Agendamento"):
    if nome and telefone and servicos_selecionados:
        # Verificação de quantidade de serviços selecionados
        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")
        # Verificar se o horário está ocupado ou bloqueado
        elif status_horarios[horario] == "ocupado" or status_horarios[horario] == "bloqueado":
            st.error("O horário está ocupado. Escolha outro.")
        else:
            # Se "Sem preferência" for escolhido, definir automaticamente o barbeiro
            if barbeiro == "Sem preferência" or not barbeiro:
                barbeiro = escolher_barbeiro(data, horario)
                if not barbeiro:
                    st.error("Não há barbeiros disponíveis para este horário. Escolha outro.")
                    st.stop()

            # Exibir um resumo do agendamento
            resumo = f"""
            Nome: {nome}
            Telefone: {telefone}
            Data: {data}
            Horário: {horario}
            Barbeiro: {barbeiro}
            Serviços: {', '.join(servicos_selecionados)}
            """

            # Salvar o agendamento no Firestore
            salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)

            # Atualizar o status do horário para 'ocupado'
            status_horarios[horario] = "ocupado"

            # Atualizar a cor de disponibilidade do horário na interface
            atualizar_cor_disponibilidade(data, horario, barbeiro, status_horarios)

            # Enviar confirmação por e-mail
            enviar_email("Agendamento Confirmado", resumo)

            # Feedback para o usuário
            st.success("Agendamento confirmado com sucesso!")
            st.info("Resumo do agendamento:\n" + resumo)
            
    else:
        st.error("Preencha todos os campos e selecione pelo menos 1 serviço.")

# Aba Cancelamento
st.subheader("Cancelar Agendamento")

telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"):
    cancelado, status_horarios_atualizado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)

    if cancelado:
        resumo_cancel = f"""
        Nome: {cancelado['nome']}
        Telefone: {cancelado['telefone']}
        Data: {cancelado['data']}
        Horário: {cancelado['horario']}
        Barbeiro: {cancelado['barbeiro']}
        Serviços: {', '.join(cancelado['servicos'])}
        """

        # Enviar e-mail de confirmação de cancelamento
        enviar_email("Agendamento Cancelado", resumo_cancel)
        st.success("Agendamento cancelado com sucesso!")
        st.info("Resumo do cancelamento:\n" + resumo_cancel)

        # Atualiza as cores dos horários
        horarios_coloridos = []
        for h in horarios:
            status = status_horarios_atualizado[h]
            if status == "ocupado":
                horarios_coloridos.append(f"🔴 {h}")
            elif status == "parcial":
                horarios_coloridos.append(f"🟡 {h}")
            else:
                horarios_coloridos.append(f"🟢 {h}")

        # Atualiza o selectbox de horários após cancelamento
        horario_index = st.selectbox("Horário", list(range(len(horarios_coloridos))),
                                     format_func=lambda x: horarios_coloridos[x])

    else:
        st.error("Não há agendamento com esse telefone nesse horário.")
