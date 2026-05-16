import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useInterviewStore = defineStore('interview', () => {
  const sessionId = ref(null)
  const stage = ref('idle')
  const triggerMode = ref('auto')
  const roundsCount = ref(0)
  const candidateName = ref('')
  const tokenUsed = ref(0)
  const tokenBudget = ref(80000)

  const transcript = ref([])
  const currentSuggestion = ref('')
  const currentRequestId = ref(null)
  const suggestionHistory = ref([])

  const wsConnected = ref(false)
  let ws = null
  let reconnectTimer = null

  function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${proto}//${location.host}/ws/interview`)

    ws.onopen = () => {
      wsConnected.value = true
      clearTimeout(reconnectTimer)
    }

    ws.onmessage = (e) => {
      try { handleMessage(JSON.parse(e.data)) } catch {}
    }

    ws.onclose = () => {
      wsConnected.value = false
      reconnectTimer = setTimeout(connect, 3000)
    }

    ws.onerror = () => ws.close()
  }

  function disconnect() {
    clearTimeout(reconnectTimer)
    if (ws) { ws.close(); ws = null }
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg))
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'session_snapshot':
        sessionId.value = msg.session_id
        stage.value = msg.stage
        triggerMode.value = msg.trigger_mode || 'auto'
        roundsCount.value = msg.rounds_count || 0
        break

      case 'transcript':
        if (msg.is_final) {
          transcript.value.push({
            source: msg.source,
            text: msg.text,
            timestamp: new Date().toLocaleTimeString('zh-CN'),
          })
        }
        break

      case 'suggestion':
        if (msg.request_id !== currentRequestId.value) {
          if (currentSuggestion.value) {
            suggestionHistory.value.push(currentSuggestion.value)
            if (suggestionHistory.value.length > 10) suggestionHistory.value.shift()
          }
          currentSuggestion.value = ''
          currentRequestId.value = msg.request_id
        }
        if (msg.is_final) {
          currentSuggestion.value = msg.text
        } else {
          currentSuggestion.value += msg.delta || ''
        }
        break

      case 'suggestion_delta':
        if (msg.request_id !== currentRequestId.value) {
          if (currentSuggestion.value) {
            suggestionHistory.value.push(currentSuggestion.value)
            if (suggestionHistory.value.length > 10) suggestionHistory.value.shift()
          }
          currentSuggestion.value = ''
          currentRequestId.value = msg.request_id
        }
        currentSuggestion.value += msg.delta || ''
        break

      case 'suggestion_final':
        if (msg.request_id === currentRequestId.value && currentSuggestion.value) {
          suggestionHistory.value.push(currentSuggestion.value)
          if (suggestionHistory.value.length > 10) suggestionHistory.value.shift()
        }
        break

      case 'status':
        stage.value = msg.stage
        break

      case 'token_usage':
        tokenUsed.value = msg.used
        tokenBudget.value = msg.budget
        break
    }
  }

  function clearSession() {
    sessionId.value = null
    stage.value = 'idle'
    triggerMode.value = 'auto'
    roundsCount.value = 0
    transcript.value = []
    currentSuggestion.value = ''
    currentRequestId.value = null
    suggestionHistory.value = []
    tokenUsed.value = 0
  }

  return {
    sessionId, stage, triggerMode, roundsCount, candidateName,
    tokenUsed, tokenBudget, transcript,
    currentSuggestion, suggestionHistory, wsConnected,
    connect, disconnect, send, clearSession,
  }
})