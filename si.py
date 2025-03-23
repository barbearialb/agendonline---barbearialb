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
            server.login(EMAIL, SENHA)
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

# Salvar agendamento no Firestore
def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    try:
        # Verificar se já existe um agendamento para esse horário diretamente no Firebase
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
    horarios_status = {h: "disponivel" for h in horarios}  # Inicializa todos os horários como disponíveis
    ocupacoes = {h: [] for h in horarios}  # Armazena os barbeiros ocupados em cada horário
    bloqueios_extra = {}  # Armazena os horários bloqueados devido ao corte + barba
    agendamentos_distribuidos = {}  # Atribui barbeiros aos horários disponíveis

    try:
        # Busca os agendamentos já existentes para o dia especificado
        docs = db.collection('agendamentos').where('data', '==', data).stream()

        for doc in docs:
            agendamento = doc.to_dict()
            h = agendamento['horario']
            b = agendamento['barbeiro']
            
            # Armazena quais barbeiros estão ocupados em cada horário
            ocupacoes[h].append(b)

            # Se o agendamento for corte + barba, bloqueia o próximo horário para o mesmo barbeiro
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
            if len(barbeiros_ocupados) == len(barbeiros):  # Todos os barbeiros ocupados
                horarios_status[h] = "ocupado"
            elif len(barbeiros_ocupados) > 0:  # Se ao menos um barbeiro está ocupado
                horarios_status[h] = "parcial"

        # Atualiza o status para horários bloqueados extras (corte + barba)
        for h, bloqueados in bloqueios_extra.items():
            if len(bloqueados) == len(barbeiros):  # Todos os barbeiros bloqueados
                horarios_status[h] = "ocupado"
            else:  # Pelo menos um barbeiro disponível, horário parcial
                horarios_status[h] = "parcial"

        # Agora, se um barbeiro for escolhido, atualizamos os horários para ele
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

# Lista de barbeiros
barbeiros = {
    "Lucas Borges": "Barbeiro 1",
    "Aluizio": "Barbeiro 2",
}

# Função para escolher automaticamente o barbeiro
def escolher_barbeiro_automatico():
    # Verifica se há barbeiros disponíveis
    if "Lucas Borges" not in barbeiros:
        return "Aluizio"  # Se Lucas não estiver disponível, escolhe Aluizio
    elif "Aluizio" not in barbeiros:
        return "Lucas Borges"  # Se Aluizio não estiver disponível, escolhe Lucas
    return "Lucas Borges"  # Se ambos estiverem disponíveis, escolhe o primeiro

# Função para atualizar a cor e status da disponibilidade
def atualizar_cor_disponibilidade(data, horario, barbeiro, status_horarios, nome, telefone, servicos_selecionados):
    # Verificação do status do horário
    status = status_horarios.get(horario, "disponivel")  # Valor padrão é "disponivel"

    # Verifica se o horário está ocupado ou bloqueado
    if status in ["ocupado", "bloqueado"]:
        return "ocupado"

    # Validação do número de serviços selecionados
    if len(servicos_selecionados) > 2:
        return "erro"  # Erro: Mais de dois serviços selecionados
    elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
        return "erro"  # Erro: Dois serviços selecionados sem incluir "Barba"

    # Define automaticamente o barbeiro, se necessário
    if barbeiro == "Sem preferência" or not barbeiro:
        barbeiro = escolher_barbeiro_automatico()
        if not barbeiro:
            return "erro"  # Erro: Sem barbeiros disponíveis

    # Salvar o agendamento no Firestore (ou outro banco de dados)
    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)

    # Atualizar o status do horário para "ocupado"
    status_horarios[horario] = "ocupado"

    # Retornar o status atualizado
    return "ocupado" if status_horarios[horario] == "ocupado" else "disponivel"

# Função que lida com a seleção do barbeiro e horário
def selecionar_barbeiro_e_horario(data, horario, barbeiro_selecionado, horarios_status):
    horario_cor = atualizar_cor_disponibilidade(data, horario, barbeiro_selecionado, horarios_status)

    if horario_cor == "ocupado":
        print("Horário ocupado! Cor vermelha.")
    elif horario_cor == "disponivel":
        print("Horário disponível! Cor verde.")
    else:
        print("Erro ao verificar o horário.")

# Definição da função bloquear_horarios
def bloquear_horarios(data, horarios):
    dia_semana = datetime.strptime(data, '%d/%m/%Y').weekday()
    horarios_status = {}

    if dia_semana < 5:  # Segunda a sexta-feira
        for h in horarios:
            hora_int = int(h[:2])
            if 12 <= hora_int < 14:
                horarios_status[h] = "bloqueado"
            else:
                horarios_status[h] = "disponível"
    elif dia_semana == 5:  # Sábado
        for h in horarios:
            horarios_status[h] = "disponível"
    else:  # Domingo
        for h in horarios:
            horarios_status[h] = "disponível"

    return horarios_status

# Função de confirmação de agendamento
def confirmar_agendamento(data, horario, barbeiro_selecionado, nome, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Verificar se já existe um agendamento para esse horário diretamente no Firebase
    if agendamento_ref.get().exists:
        st.error(f"Não é possível agendar para o horário {horario} no dia {data}, pois ele já está ocupado.")
        return
    
    try:
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
    
def confirmar_agendamento(data, horario, barbeiro_selecionado, nome, telefone):
    # Verificar a disponibilidade do horário
    if verificar_disponibilidade(data, horario):
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

            # Atualizar a cor do horário de acordo com o barbeiro
            status_horarios, erro = obter_status_horarios(data)

            if status_horarios is None:
                st.error(f"Erro ao obter status dos horários: {erro}")
                return

            status = status_horarios.get(horario, "disponivel")
            if status == "ocupado":
                cor = "🔴"
                mensagem_status = f"O horário {horario} está ocupado. {barbeiro_selecionado} foi alocado."
            elif status == "parcial":
                cor = "🟡"
                mensagem_status = f"O horário {horario} está parcialmente ocupado. {barbeiro_selecionado} será alocado."
            else:
                cor = "🟢"
                mensagem_status = f"O horário {horario} está disponível. {barbeiro_selecionado} foi alocado."

            st.markdown(f"{cor} {mensagem_status}")

        except Exception as e:
            st.error(f"Erro ao confirmar o agendamento: {e}")
    else:
        st.error(f"Não é possível agendar para o horário {horario} no dia {data}, pois ele está ocupado.")

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
barbeiro = st.selectbox("Escolha o barbeiro", ["Sem preferência"] + list(barbeiros.keys()))

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
    # Certifique-se de que servicos_selecionados foi definida anteriormente
# Exemplo de valores que você quer passar
servicos_selecionados = list(servicos.keys())  # Lista com os serviços que o cliente escolheu

# Chamada da função para atualizar a disponibilidade
status_cor = atualizar_cor_disponibilidade(data, horario, barbeiro, status_horarios, nome, telefone, servicos_selecionados)

# Mudar a cor dinamicamente dependendo do status
if status_cor == "ocupado":
    st.markdown(f"<p style='color:red;'>Horário {horario} está ocupado.</p>", unsafe_allow_html=True)
elif status_cor == "disponivel":
    st.markdown(f"<p style='color:green;'>Horário {horario} está disponível.</p>", unsafe_allow_html=True)
elif status_cor == "erro":
    st.markdown(f"<p style='color:orange;'>Erro ao verificar disponibilidade.</p>", unsafe_allow_html=True)
else:
    st.markdown(f"<p style='color:yellow;'>Escolha um barbeiro para atualizar a disponibilidade.</p>", unsafe_allow_html=True)

# Exibir preços dos serviços
servicos_com_preco = {s: f"R$ {p}" for s, p in servicos.items()}
st.write("Preços dos serviços:")
for s, p in servicos_com_preco.items():
    st.write(f"{s}: {p}")


# Seleção de serviços
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Validação e Agendamento
# Função de confirmação de agendamento

if st.button("Confirmar Agendamento"): 
    if nome and telefone and servicos_selecionados:

        if "Sem preferência" in barbeiro:
            barbeiro = "Sem preferência"

        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")
        else:
            with st.spinner("Verificando disponibilidade..."):
                if verificar_disponibilidade(data, horario):
                    resumo = f"""
                    Nome: {nome}
                    Telefone: {telefone}
                    Data: {data}
                    Horário: {horario}
                    Barbeiro: {barbeiro}
                    Serviços: {', '.join(servicos_selecionados)}
                    """
                    
                    # Salvar agendamento
                    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)

                    # Enviar e-mail
                    enviar_email("Agendamento Confirmado", resumo)

                    # Atualizar status do horário e mudar cor das bolinhas
                    status_horarios[horario] = "ocupado"

                    # Exibir cores atualizadas
                    horarios_coloridos = []
                    for h in horarios:
                        status = status_horarios.get(h, "disponível")
                        if status == "ocupado":
                            horarios_coloridos.append(f"🔴 {h}")
                        elif status == "parcial":
                            horarios_coloridos.append(f"🟡 {h}")
                        else:
                            horarios_coloridos.append(f"🟢 {h}")

                    # Mostrar horários coloridos na interface
                    st.success("Agendamento confirmado com sucesso!")
                    st.info("Resumo do agendamento:\n" + resumo)
                    st.markdown("### ✅ Seu agendamento foi confirmado com sucesso!")
                    st.markdown("### Status dos horários:")
                    st.write(horarios_coloridos)
                else:
                    st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário.")
    else:
        st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")


# Aba Cancelamento
st.subheader("Cancelar Agendamento")

telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"): 
    with st.spinner("Processando cancelamento..."):

        # Chama a função para cancelar o agendamento
        cancelado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)

        if cancelado:
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            
            # Enviar notificação por e-mail sobre o cancelamento
            enviar_email("Agendamento Cancelado", resumo_cancelamento)

            # Atualizar o status do horário após o cancelamento (voltar para "disponível")
            status_horarios[cancelado['horario']] = "disponível"

            # Gerar a lista de horários coloridos novamente
            horarios_coloridos = []
            for h in horarios:
                status = status_horarios.get(h, "disponível")
                if status == "ocupado":
                    horarios_coloridos.append(f"🔴 {h}")
                elif status == "parcial":
                    horarios_coloridos.append(f"🟡 {h}")
                else:
                    horarios_coloridos.append(f"🟢 {h}")

            # Exibir mensagens de sucesso e o resumo do cancelamento
            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
            st.markdown("### ✅ Seu agendamento foi cancelado com sucesso!")
            st.markdown("### Status dos horários após cancelamento:")
            st.write(horarios_coloridos)

        else:
            st.error("Não há agendamento para o telefone informado nesse horário.")
