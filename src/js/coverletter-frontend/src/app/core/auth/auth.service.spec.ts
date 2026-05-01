/// <reference types="jasmine" />

import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { AuthService } from './auth.service';

describe('AuthService', () => {
  let service: AuthService;
  let routerSpy: jasmine.SpyObj<Router>;

  beforeEach(() => {
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    (routerSpy.navigate as jasmine.Spy).and.returnValue(Promise.resolve(true));

    TestBed.configureTestingModule({
      providers: [
        AuthService,
        { provide: Router, useValue: routerSpy },
      ],
    });

    service = TestBed.inject(AuthService);
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('getToken returns null when no token is stored', () => {
    expect(service.getToken()).toBeNull();
  });

  it('setToken persists the token in localStorage', () => {
    service.setToken('my-token');
    expect(localStorage.getItem('token')).toBe('my-token');
  });

  it('getToken returns the stored token', () => {
    service.setToken('abc123');
    expect(service.getToken()).toBe('abc123');
  });

  it('isAuthenticated returns false when no token is stored', () => {
    expect(service.isAuthenticated()).toBeFalse();
  });

  it('isAuthenticated returns true when a token is stored', () => {
    service.setToken('valid-token');
    expect(service.isAuthenticated()).toBeTrue();
  });

  it('getHeaders returns empty headers when not authenticated', () => {
    const headers = service.getHeaders();
    expect(headers.get('Authorization')).toBeNull();
  });

  it('getHeaders returns Bearer header when authenticated', () => {
    service.setToken('my-token');
    const headers = service.getHeaders();
    expect(headers.get('Authorization')).toBe('Bearer my-token');
  });

  it('logout removes the token from localStorage', () => {
    service.setToken('some-token');
    service.logout(false);
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('logout navigates to /login by default', () => {
    service.setToken('some-token');
    service.logout();
    expect(routerSpy.navigate).toHaveBeenCalledWith(['/login']);
  });

  it('logout does not navigate when redirectToLogin is false', () => {
    service.setToken('some-token');
    service.logout(false);
    expect(routerSpy.navigate).not.toHaveBeenCalled();
  });
});
