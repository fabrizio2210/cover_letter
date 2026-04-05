import { Routes } from '@angular/router';
import { LoginComponent } from './login.component';
import { DashboardComponent } from './dashboard.component';
import { DashboardOverviewComponent } from './dashboard-overview.component';
import { FieldsListComponent } from './fields-list.component';
import { IdentitiesListComponent } from './identities-list.component';
import { CoverLettersListComponent } from './coverletters-list.component';
import { CoverLettersDetailComponent } from './coverletters-detail.component';
import { CompaniesRecipientsComponent } from './companies-recipients.component';
import { authGuard } from './auth.guard';

export const routes: Routes = [
    { path: 'login', component: LoginComponent },

    // Dashboard acts as a shell with sidebar nav and router-outlet for child pages
    {
        path: 'dashboard',
        component: DashboardComponent,
        canActivate: [authGuard],
        children: [
            { path: '', component: DashboardOverviewComponent }, // Overview page with stats & opportunities
            { path: 'fields', component: FieldsListComponent },
            { path: 'identities', component: IdentitiesListComponent },
            { path: 'cover-letters', component: CoverLettersListComponent },
            { path: 'cover-letters/:id', component: CoverLettersDetailComponent },
            { path: 'companies', component: CompaniesRecipientsComponent }
        ]
    },

    // Keep top-level redirect to the login page by default
    { path: '', redirectTo: '/login', pathMatch: 'full' }
];
