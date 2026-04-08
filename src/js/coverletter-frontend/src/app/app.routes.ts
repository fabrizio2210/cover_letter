import { Routes } from '@angular/router';
import { LoginComponent } from './login.component';
import { DashboardComponent } from './dashboard.component';
import { DashboardOverviewComponent } from './dashboard-overview.component';
import { JobDiscoveryComponent } from './job-discovery.component';
import { IdentitiesComponent } from './identities.component';
import { CoverLettersListComponent } from './coverletters-list.component';
import { CompaniesRecipientsComponent } from './companies-recipients.component';
import { SettingsComponent } from './settings.component';
import { LetterEditorComponent } from './letter-editor.component';
import { authGuard } from './auth.guard';

export const routes: Routes = [
    { path: 'login', component: LoginComponent },
    { path: 'settings', redirectTo: '/dashboard/settings', pathMatch: 'full' },

    // Dashboard acts as a shell with sidebar nav and router-outlet for child pages
    {
        path: 'dashboard',
        component: DashboardComponent,
        canActivate: [authGuard],
        children: [
            { path: '', component: DashboardOverviewComponent }, // Overview page with stats & opportunities
            { path: 'job-discovery', component: JobDiscoveryComponent },
            { path: 'identities', component: IdentitiesComponent },
            { path: 'settings', component: SettingsComponent },
            { path: 'fields', redirectTo: 'settings', pathMatch: 'full' },
            { path: 'letter-editor/:id', component: LetterEditorComponent },
            { path: 'cover-letters', component: CoverLettersListComponent },
            { path: 'cover-letters/:id', redirectTo: 'letter-editor/:id', pathMatch: 'full' },
            { path: 'companies', component: CompaniesRecipientsComponent }
        ]
    },

    // Keep top-level redirect to the login page by default
    { path: '', redirectTo: '/login', pathMatch: 'full' }
];
