import {createRouter,createWebHistory} from 'vue-router';
import Dashboard from './views/Dashboard.vue';
import Profile from './views/Profile.vue';
import Agent from './views/Agent.vue';
import Versions from './views/Versions.vue';
import About from './views/About.vue';
import Evaluation from './views/Evaluation.vue';
import BidDecision from './views/BidDecision.vue';
import CompetitionDetail from './views/CompetitionDetail.vue';
export default createRouter({history:createWebHistory(),routes:[
 {path:'/',component:Dashboard},
 {path:'/companies/:companyId/profile',component:Profile},
 {path:'/agent',component:Agent},
 {path:'/bid-decision',component:BidDecision},
 {path:'/bid-decision/projects/:projectId/competition',name:'competition-detail',component:CompetitionDetail},
 {path:'/evaluation',component:Evaluation},
 {path:'/versions',component:Versions},
 {path:'/about',component:About}
]});
