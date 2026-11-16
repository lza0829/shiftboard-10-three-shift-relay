import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './pages/Dashboard.vue'
import Shifts from './pages/Shifts.vue'
import Batches from './pages/Batches.vue'
import HandoverDesk from './pages/HandoverDesk.vue'
import HandoverLogs from './pages/HandoverLogs.vue'
import Reconcile from './pages/Reconcile.vue'
import RelayPlans from './pages/RelayPlans.vue'
import RelayPlanDetail from './pages/RelayPlanDetail.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Dashboard },
    { path: '/shifts', component: Shifts },
    { path: '/batches', component: Batches },
    { path: '/relay', component: RelayPlans },
    { path: '/relay/:id', component: RelayPlanDetail },
    { path: '/handover', component: HandoverDesk },
    { path: '/logs', component: HandoverLogs },
    { path: '/reconcile', component: Reconcile },
  ],
})
