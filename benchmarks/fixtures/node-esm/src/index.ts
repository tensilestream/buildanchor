import Fastify from 'fastify';
import { z } from 'zod';

const server = Fastify();
server.listen({ port: 8080 });
