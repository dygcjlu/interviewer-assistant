import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('../views/HomeView.vue') },
  { path: '/prepare', component: () => import('../views/PrepareView.vue') },
  { path: '/console', component: () => import('../views/ConsoleView.vue') },
  { path: '/report', component: () => import('../views/ReportView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})