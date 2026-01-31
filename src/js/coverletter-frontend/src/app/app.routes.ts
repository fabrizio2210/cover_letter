import { Routes } from '@angular/router';
import { LoginComponent } from './login.component';
import { DashboardComponent } from './dashboard.component';
import { FieldsListComponent } from './fields-list.component';
import { IdentitiesListComponent } from './identities-list.component';
import { CoverLettersListComponent } from './coverletters-list.component';
import { CoverLettersDetailComponent } from './coverletters-detail.component';
import { CompaniesListComponent } from './companies-list.component';
import { RecipientsListComponent } from './recipients-list.component';

export const routes: Routes = [
    { path: 'login', component: LoginComponent },

    // Dashboard acts as a shell with a top nav and router-outlet for child pages
    {
        path: 'dashboard',
        component: DashboardComponent,
        children: [
            { path: '', redirectTo: 'recipients', pathMatch: 'full' },
            { path: 'recipients', component: RecipientsListComponent },
            { path: 'fields', component: FieldsListComponent },
            { path: 'identities', component: IdentitiesListComponent },
            { path: 'cover-letters', component: CoverLettersListComponent },
            { path: 'cover-letters/:id', component: CoverLettersDetailComponent },
            { path: 'companies', component: CompaniesListComponent }
        ]
    },

    // Keep top-level redirect to the login page by default
    { path: '', redirectTo: '/login', pathMatch: 'full' }
];
