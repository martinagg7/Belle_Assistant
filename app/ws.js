// ws.js — cliente WebSocket Belle → pantalla

const WS_URL  = `ws://${window.location.host}/ws`;
const API_URL = `http://${window.location.host}`;
const RECORDATORIO_TIMEOUT = 15000;
const EMERGENCIA_TIMEOUT   = 5000;
const FOTO_TIMEOUT         = 15000;

let ws;
let timerRecordatorio        = null;
let timerEmergencia          = null;
let timerFoto                = null;
let timerRecordatorioFamilia = null;
let timerTelegramMensaje     = null;
let timerChat                = null;
let emergenciaActiva         = false;
let _audioWaveRAF            = null;

// ── Onda sinusoidal animada ───────────────────────────────────────────
function iniciarOndaAudio() {
    const canvas = document.getElementById('audio-wave-canvas');
    if (!canvas) return;

    // Dimensiones reales del canvas = tamaño CSS × devicePixelRatio
    const dpr = window.devicePixelRatio || 1;
    canvas.width  = canvas.offsetWidth  * dpr;
    canvas.height = canvas.offsetHeight * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const W  = canvas.offsetWidth;
    const H  = canvas.offsetHeight;
    const cy = H / 2;

    // Capas: { amp, freq, speed, phase, color, alpha, lineW, blur }
    const capas = [
        { amp:55, freq:0.016, speed:1.2, phase:0.0, color:'#00cfff', alpha:0.85, lineW:2.2, blur:18 },
        { amp:38, freq:0.024, speed:1.8, phase:1.3, color:'#4de8ff', alpha:0.55, lineW:1.4, blur:12 },
        { amp:65, freq:0.011, speed:0.9, phase:2.5, color:'#0077ff', alpha:0.40, lineW:3.0, blur:22 },
        { amp:28, freq:0.032, speed:2.4, phase:0.7, color:'#00eeff', alpha:0.45, lineW:1.2, blur:10 },
        { amp:48, freq:0.019, speed:1.5, phase:3.8, color:'#1a90ff', alpha:0.30, lineW:2.8, blur:28 },
        { amp:20, freq:0.028, speed:2.0, phase:1.9, color:'#80dfff', alpha:0.25, lineW:1.0, blur: 8 },
    ];

    let t = 0;

    function dibujar() {
        ctx.clearRect(0, 0, W, H);

        capas.forEach(c => {
            ctx.beginPath();
            for (let x = 0; x <= W; x++) {
                const y = cy + c.amp * Math.sin(c.freq * x + t * c.speed * 0.04 + c.phase);
                x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            }
            ctx.strokeStyle    = c.color;
            ctx.lineWidth      = c.lineW;
            ctx.globalAlpha    = c.alpha;
            ctx.shadowColor    = c.color;
            ctx.shadowBlur     = c.blur;
            ctx.stroke();
        });

        ctx.globalAlpha = 1;
        ctx.shadowBlur  = 0;
        t++;
        _audioWaveRAF = requestAnimationFrame(dibujar);
    }

    dibujar();
}

function detenerOndaAudio() {
    if (_audioWaveRAF) {
        cancelAnimationFrame(_audioWaveRAF);
        _audioWaveRAF = null;
    }
    const canvas = document.getElementById('audio-wave-canvas');
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
}

// ── Emoji tiempo ─────────────────────────────────────────────────────
function getWeatherEmoji(condicion) {
    const c = (condicion || "").toLowerCase();
    if (c.includes("tormenta") || c.includes("thunder") || c.includes("storm"))          return "⛈️";
    if (c.includes("lluvia")   || c.includes("rain")    || c.includes("llovizna") || c.includes("drizzle")) return "🌧️";
    if (c.includes("nieve")    || c.includes("snow"))                                     return "❄️";
    if (c.includes("niebla")   || c.includes("fog")     || c.includes("mist") || c.includes("bruma")) return "🌫️";
    if (c.includes("nub")      || c.includes("cloud")   || c.includes("overcast") || c.includes("nublado")) return "☁️";
    if (c.includes("parcial")  || c.includes("partly"))                                   return "⛅";
    if (c.includes("sol")      || c.includes("despejado") || c.includes("soleado") || c.includes("clear")) return "☀️";
    return "🌤️";
}

// ── Helpers ───────────────────────────────────────────────────────────
function capitalizar(t) {
    if (!t) return "";
    return t.charAt(0).toUpperCase() + t.slice(1);
}

function ocultarTodo() {
    detenerOndaAudio();
    ["vista-standby","vista-thinking","vista-escuchando","vista-time","vista-weather",
     "vista-radio","vista-news","vista-recordatorio","vista-familia",
     "vista-emergencia","vista-foto-telegram","vista-recordatorio-familiar",
     "vista-telegram-mensaje","vista-medicacion","vista-chat","vista-audio-chat"]
    .forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = "none";
    });
}

// ── Pantallas ─────────────────────────────────────────────────────────
function mostrarStandby() {
    if (emergenciaActiva) return;
    ocultarTodo();
    document.getElementById("vista-standby").style.display = "flex";
}

function mostrarThinking() {
    ocultarTodo();
    document.getElementById("vista-thinking").style.display = "flex";
}

function mostrarEscuchando() {
    ocultarTodo();
    document.getElementById("vista-escuchando").style.display = "flex";
}

function mostrarTime(data) {
    ocultarTodo();
    document.getElementById("time-hora").textContent  = data.hora  || "--:--";
    document.getElementById("time-fecha").textContent = data.fecha || "";
    document.getElementById("vista-time").style.display = "flex";
}

function mostrarWeather(data) {
    ocultarTodo();
    document.getElementById("weather-icon").textContent = getWeatherEmoji(data.condicion);
    document.getElementById("weather-temp").textContent = data.temp || "--";
    document.getElementById("weather-ciudad").textContent    = data.ciudad   || "";
    document.getElementById("weather-condicion").textContent = capitalizar(data.condicion || "");
    const maxmin = (data.maxima && data.minima) ? `Max ${data.maxima}   Min ${data.minima}` : "";
    document.getElementById("weather-maxmin").textContent = maxmin;
    document.getElementById("vista-weather").style.display = "flex";
}

function mostrarRadio(data) {
    ocultarTodo();
    const tipo = data.tipo === "musica" ? "Música" : "Noticias de Radio";
    const query = data.query ? `  ·  ${data.query}` : "";
    document.getElementById("radio-tipo").textContent = tipo + query;
    document.getElementById("vista-radio").style.display = "flex";
}

function mostrarNews(data) {
    ocultarTodo();
    document.getElementById("news-categoria").textContent = capitalizar(data.categoria || "General");
    document.getElementById("vista-news").style.display = "flex";
}

function mostrarRecordatorio(data) {
    if (timerRecordatorio) clearTimeout(timerRecordatorio);
    ocultarTodo();
    document.getElementById("recordatorio-texto").textContent = data.texto || "";
    document.getElementById("recordatorio-hora").textContent  = data.hora  || "";
    document.getElementById("vista-recordatorio").style.display = "flex";
    timerRecordatorio = setTimeout(mostrarStandby, RECORDATORIO_TIMEOUT);
}

function mostrarFamiliar(data) {
    ocultarTodo();
    document.getElementById("familia-nombre").textContent    = capitalizar(data.nombre);
    document.getElementById("familia-relacion").textContent  = capitalizar(data.relacion);
    document.getElementById("familia-descripcion").textContent = data.descripcion || "";

    const telDiv = document.getElementById("familia-telefono");
    telDiv.style.display = data.telefono ? "block" : "none";
    if (data.telefono) telDiv.textContent = data.telefono;

    const img         = document.getElementById("familia-foto");
    const placeholder = document.getElementById("familia-foto-placeholder");
    if (data.foto_url) {
        img.src                   = data.foto_url;
        img.style.display         = "block";
        placeholder.style.display = "none";
    } else {
        img.style.display         = "none";
        placeholder.style.display = "flex";
    }
    document.getElementById("vista-familia").style.display = "flex";
}

function mostrarEmergencia() {
    if (timerEmergencia) clearTimeout(timerEmergencia);
    emergenciaActiva = true;
    ocultarTodo();
    document.getElementById("vista-emergencia").style.display = "flex";
    timerEmergencia = setTimeout(() => {
        emergenciaActiva = false;
        mostrarStandby();
    }, EMERGENCIA_TIMEOUT);
}

function mostrarFotoTelegram(data) {
    if (timerFoto) clearTimeout(timerFoto);
    ocultarTodo();
    document.getElementById("foto-telegram-img").src             = data.foto_url || "";
    document.getElementById("foto-telegram-nombre").textContent  = data.nombre   || "";
    document.getElementById("foto-telegram-caption").textContent = data.caption  || "";
    document.getElementById("vista-foto-telegram").style.display = "flex";
    timerFoto = setTimeout(mostrarStandby, FOTO_TIMEOUT);
}

function mostrarRecordatorioFamiliar(data) {
    if (timerRecordatorioFamilia) clearTimeout(timerRecordatorioFamilia);
    ocultarTodo();
    document.getElementById("recordatorio-familiar-creado").textContent = data.creado_por
        ? `${capitalizar(data.creado_por)} te ha añadido un recordatorio`
        : "Nuevo recordatorio";
    document.getElementById("recordatorio-familiar-texto").textContent = data.texto || "";
    document.getElementById("vista-recordatorio-familiar").style.display = "flex";
    timerRecordatorioFamilia = setTimeout(mostrarStandby, RECORDATORIO_TIMEOUT);
}

function mostrarMedicacion(data) {
    ocultarTodo();
    document.getElementById("medicacion-nombre").textContent = data.nombre || "";
    document.getElementById("medicacion-dosis").textContent  = data.dosis  || "";
    document.getElementById("vista-medicacion").style.display = "flex";
}

function mostrarAudioChat(data) {
    detenerOndaAudio();
    ocultarTodo();
    document.getElementById("audio-chat-nombre").textContent = capitalizar(data.nombre || "Familiar");
    document.getElementById("vista-audio-chat").style.display = "flex";
    // Espera un frame para que el canvas tenga dimensiones reales antes de dibujar
    requestAnimationFrame(iniciarOndaAudio);
}

function mostrarChat(data) {
    if (timerChat) clearTimeout(timerChat);
    ocultarTodo();
    document.getElementById("chat-nombre").textContent = capitalizar(data.nombre || "Familiar");
    document.getElementById("vista-chat").style.display = "flex";
    timerChat = setTimeout(mostrarStandby, 20000);
}

function mostrarTelegramMensaje(data) {
    if (timerTelegramMensaje) clearTimeout(timerTelegramMensaje);
    ocultarTodo();
    document.getElementById("telegram-mensaje-nombre").textContent = capitalizar(data.nombre || "");
    document.getElementById("vista-telegram-mensaje").style.display = "flex";
    timerTelegramMensaje = setTimeout(mostrarStandby, FOTO_TIMEOUT);
}

// ── Router de eventos ─────────────────────────────────────────────────
function manejarEvento(msg) {
    switch (msg.event) {
        case "show_thinking":              mostrarThinking();                          break;
        case "show_escuchando":            mostrarEscuchando();                        break;
        case "show_time":                  mostrarTime(msg.data              || {});   break;
        case "show_weather":               mostrarWeather(msg.data           || {});   break;
        case "show_radio":                 mostrarRadio(msg.data             || {});   break;
        case "show_news":                  mostrarNews(msg.data              || {});   break;
        case "show_family":                mostrarFamiliar(msg.data          || {});   break;
        case "show_recordatorio":          mostrarRecordatorio(msg.data      || {});   break;
        case "show_emergencia":            mostrarEmergencia();                        break;
        case "show_foto_telegram":         mostrarFotoTelegram(msg.data      || {});   break;
        case "show_recordatorio_familiar": mostrarRecordatorioFamiliar(msg.data || {}); break;
        case "show_medicacion":            mostrarMedicacion(msg.data           || {}); break;
        case "show_mensaje_telegram":       mostrarTelegramMensaje(msg.data   || {});   break;
        case "show_chat":                   mostrarChat(msg.data              || {});   break;
        case "show_audio_chat":             mostrarAudioChat(msg.data         || {});   break;
        case "standby":                    mostrarStandby();                           break;
        default: console.log("[WS] evento desconocido:", msg.event);
    }
}

// ── WebSocket ─────────────────────────────────────────────────────────
function conectar() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log("[WS] conectado a Belle");
        setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000);
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            console.log("[WS] evento:", msg.event);
            manejarEvento(msg);
        } catch (e) {
            console.warn("[WS] mensaje no JSON:", event.data);
        }
    };

    ws.onclose = () => {
        console.warn("[WS] desconectado, reintentando en 3s...");
        setTimeout(conectar, 3000);
    };

    ws.onerror = (err) => console.error("[WS] error:", err);
}

// ── Botón stop radio ──────────────────────────────────────────────────
document.getElementById('btn-stop-radio').addEventListener('click', (e) => {
    e.stopPropagation();
    fetch(`${API_URL}/audio/stop`, { method: 'POST' }).catch(() => {});
});

// ── Tap para activar sin wake word ───────────────────────────────────
const TAP_EVT = ('ontouchstart' in window) ? 'touchstart' : 'click';
document.body.addEventListener(TAP_EVT, () => {
    const standby = document.getElementById('vista-standby');
    if (standby && standby.style.display !== 'none' && ws && ws.readyState === 1) {
        ws.send('tap_activar');
    }
});

fetch(`${API_URL}/interno/ultimo-evento`)
    .then(r => r.json())
    .then(msg => manejarEvento(msg))
    .catch(() => {});

conectar();
