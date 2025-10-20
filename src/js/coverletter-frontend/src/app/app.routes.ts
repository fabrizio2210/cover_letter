import { Routes } from '@angular/router';
import { LoginComponent } from './login.component';
import { DashboardComponent } from './dashboard.component';
import { FieldsListComponent } from './fields-list.component';
import { IdentitiesListComponent } from './identities-list.component';
import { CoverLettersListComponent } from './coverletters-list.component';
import { CompaniesListComponent } from './companies-list.component';

export const routes: Routes = [
    { path: 'login', component: LoginComponent },

    // The Dashboard component continues to serve the Recipients list UI.
    { path: 'dashboard', component: DashboardComponent },

    // Explicit top-level routes for each resource so they are deep-linkable:
    { path: 'recipients', component: DashboardComponent }, // dashboard contains the recipients table
    { path: 'fields', component: FieldsListComponent },
    { path: 'identities', component: IdentitiesListComponent },
    { path: 'cover-letters', component: CoverLettersListComponent },
    { path: 'companies', component: CompaniesListComponent },

    { path: '', redirectTo: '/login', pathMatch: 'full' }
];
