// Steady, realistic traffic through the API gateway: owners, pets, visits and vets.
import http from 'k6/http';
import { sleep } from 'k6';

const BASE = 'http://api-gateway:8080';

export const options = {
  vus: 5,
  duration: '24h',
};

export default function () {
  const owner = Math.floor(Math.random() * 10) + 1;
  http.get(`${BASE}/api/customer/owners`);
  http.get(`${BASE}/api/gateway/owners/${owner}`); // gateway calls customers + visits
  http.get(`${BASE}/api/vet/vets`);
  http.get(`${BASE}/api/customer/petTypes`);
  sleep(1);
}
